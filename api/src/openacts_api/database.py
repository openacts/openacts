"""Read-only access to projection release identity."""

from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row
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


class ProjectionUnavailable(RuntimeError):
    """Raised when the active projection cannot be served safely."""


@dataclass(frozen=True)
class ActiveRelease:
    release_tag: str
    commit_sha: str
    canonical_schema_versions: tuple[str, ...]


class ProjectionDatabase:
    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool(
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

    def active_release(self) -> ActiveRelease:
        try:
            with self._pool.connection() as connection:
                row = connection.execute(_ACTIVE_RELEASE_SQL).fetchone()
        except ProjectionUnavailable:
            raise
        except (psycopg.Error, PoolTimeout) as exc:
            raise ProjectionUnavailable("projection database is unavailable") from exc

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
