"""Read-only access to the projected corpus."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

SUPPORTED_PROJECTION_SCHEMA_VERSION = 1
SUPPORTED_CANONICAL_SCHEMA_VERSIONS = frozenset({"0.1.0"})

_ACTIVE_RELEASE_SQL = """
    SELECT
        current_setting('transaction_read_only') AS transaction_read_only,
        release.release_tag,
        release.commit_sha,
        release.canonical_schema_versions,
        release.projection_schema_version,
        release.import_state
    FROM projection_state AS state
    JOIN corpus_releases AS release
      ON release.release_tag = state.active_release_tag
    WHERE state.singleton IS TRUE
"""

_LIST_ACTS_SQL = """
    SELECT
        act_id,
        official_title,
        short_title,
        year,
        number,
        citation,
        text_kind,
        status,
        checked_through_date
    FROM acts
    WHERE release_tag = %s
    ORDER BY title_keys[1], year DESC, act_id
    OFFSET %s
    LIMIT %s
"""

_GET_ACT_SQL = """
    SELECT canonical_record
    FROM acts
    WHERE release_tag = %s AND act_id = %s
"""

_GET_SOURCES_SQL = """
    SELECT source_id, canonical_record
    FROM sources
    WHERE release_tag = %s AND source_id = ANY(%s)
"""

_GET_SOURCE_SQL = """
    SELECT canonical_record
    FROM sources
    WHERE release_tag = %s AND source_id = %s
"""


class ProjectionUnavailable(RuntimeError):
    """Raised when the active projection cannot be served safely."""


@dataclass(frozen=True)
class ActiveRelease:
    release_tag: str
    commit_sha: str
    canonical_schema_versions: tuple[str, ...]


class ProjectionReader(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def active_release(self) -> ActiveRelease: ...

    def list_acts(
        self, release_tag: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_act(
        self, release_tag: str, act_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None: ...

    def get_source(self, release_tag: str, source_id: str) -> dict[str, Any] | None: ...


def _ordered_source_ids(act: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(ref["source_id"] for ref in act["source_refs"]))


class ProjectionDatabase:
    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool[psycopg.Connection[DictRow]](
            conninfo=database_url,
            min_size=0,
            max_size=5,
            timeout=5,
            open=False,
            kwargs={
                "options": "-c default_transaction_read_only=on",
                "row_factory": dict_row,
            },
        )

    def open(self) -> None:
        # min_size=0 keeps liveness independent from PostgreSQL availability.
        self._pool.open()

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[DictRow]]:
        try:
            with self._pool.connection() as connection:
                yield connection
        except (psycopg.Error, PoolTimeout) as exc:
            raise ProjectionUnavailable("projection database is unavailable") from exc

    def active_release(self) -> ActiveRelease:
        with self._connection() as connection:
            row = connection.execute(_ACTIVE_RELEASE_SQL).fetchone()

        if row is None or row["import_state"] != "ready":
            raise ProjectionUnavailable("no ready active corpus release")
        if row["transaction_read_only"] != "on":
            raise ProjectionUnavailable("database session is not read-only")
        if row["projection_schema_version"] != SUPPORTED_PROJECTION_SCHEMA_VERSION:
            raise ProjectionUnavailable("projection schema version is unsupported")

        canonical_versions = tuple(sorted(set(row["canonical_schema_versions"])))
        if not canonical_versions or not set(canonical_versions).issubset(
            SUPPORTED_CANONICAL_SCHEMA_VERSIONS
        ):
            raise ProjectionUnavailable("canonical schema version is unsupported")

        return ActiveRelease(
            release_tag=row["release_tag"],
            commit_sha=row["commit_sha"],
            canonical_schema_versions=canonical_versions,
        )

    def list_acts(
        self, release_tag: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        with self._connection() as connection:
            total_row = connection.execute(
                "SELECT count(*) AS total FROM acts WHERE release_tag = %s",
                (release_tag,),
            ).fetchone()
            if total_row is None:
                raise ProjectionUnavailable("act count query returned no row")
            total = total_row["total"]
            rows = connection.execute(
                _LIST_ACTS_SQL,
                (release_tag, offset, limit),
            ).fetchall()
        return list(rows), total

    def get_act(
        self, release_tag: str, act_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        with self._connection() as connection:
            row = connection.execute(_GET_ACT_SQL, (release_tag, act_id)).fetchone()
            if row is None:
                return None

            act = row["canonical_record"]
            source_ids = _ordered_source_ids(act)
            source_rows = connection.execute(
                _GET_SOURCES_SQL,
                (release_tag, source_ids),
            ).fetchall()

        sources_by_id = {
            source["source_id"]: source["canonical_record"] for source in source_rows
        }
        if set(sources_by_id) != set(source_ids):
            raise ProjectionUnavailable("act source projection is incomplete")
        return act, [sources_by_id[source_id] for source_id in source_ids]

    def get_source(self, release_tag: str, source_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                _GET_SOURCE_SQL,
                (release_tag, source_id),
            ).fetchone()
        return None if row is None else row["canonical_record"]
