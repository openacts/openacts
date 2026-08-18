"""Read-only access to the projected corpus."""

import re
import unicodedata
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

_GET_ACT_CONTENTS_SQL = """
    SELECT
        provision.provision_id,
        provision.parent_provision_id,
        provision.node_type,
        provision.display_label,
        provision.heading,
        provision.sibling_order AS "order",
        provision.sequence,
        provision.depth,
        jsonb_array_length(
            provision.canonical_record -> 'content_blocks'
        ) > 0 AS has_content,
        EXISTS (
            SELECT 1
            FROM provisions AS child
            WHERE child.release_tag = provision.release_tag
              AND child.act_id = provision.act_id
              AND child.parent_provision_id = provision.provision_id
        ) AS has_children
    FROM provisions AS provision
    WHERE provision.release_tag = %s AND provision.act_id = %s
    ORDER BY provision.sequence
"""

_GET_PROVISION_SQL = """
    WITH RECURSIVE
    provision_rows AS NOT MATERIALIZED (
        SELECT
            provision.release_tag,
            provision.provision_id,
            provision.act_id,
            provision.parent_provision_id,
            provision.sequence,
            provision.depth,
            provision.source_ids,
            provision.canonical_record,
            jsonb_build_object(
                'provision_id', provision.provision_id,
                'act_id', provision.act_id,
                'node_type', provision.node_type,
                'display_label', provision.display_label,
                'heading', provision.heading
            ) AS summary
        FROM provisions AS provision
        WHERE provision.release_tag = %s
    ),
    act_rows AS NOT MATERIALIZED (
        SELECT
            act.release_tag,
            act.act_id,
            jsonb_build_object(
                'act_id', act.act_id,
                'official_title', act.official_title,
                'short_title', act.short_title,
                'year', act.year,
                'number', act.number,
                'citation', act.citation,
                'text_kind', act.text_kind,
                'status', act.status,
                'checked_through_date', act.checked_through_date
            ) AS summary
        FROM acts AS act
        WHERE act.release_tag = %s
    ),
    requested AS (
        SELECT *
        FROM provision_rows
        WHERE provision_id = %s
    ),
    subtree AS (
        SELECT *
        FROM requested
        UNION ALL
        SELECT child.*
        FROM provision_rows AS child
        JOIN subtree AS parent
          ON child.release_tag = parent.release_tag
         AND child.act_id = parent.act_id
         AND child.parent_provision_id = parent.provision_id
    ),
    ancestors AS (
        SELECT parent.*
        FROM provision_rows AS parent
        JOIN requested AS child
          ON parent.release_tag = child.release_tag
         AND parent.act_id = child.act_id
         AND parent.provision_id = child.parent_provision_id
        UNION ALL
        SELECT parent.*
        FROM provision_rows AS parent
        JOIN ancestors AS child
          ON parent.release_tag = child.release_tag
         AND parent.act_id = child.act_id
         AND parent.provision_id = child.parent_provision_id
    ),
    referenced_source_ids AS (
        SELECT DISTINCT unnest(member.source_ids) AS source_id
        FROM subtree AS member
    )
    SELECT
        act.summary AS act,
        requested.canonical_record AS provision,
        COALESCE((
            SELECT jsonb_agg(
                descendant.canonical_record ORDER BY descendant.sequence
            )
            FROM subtree AS descendant
            WHERE descendant.provision_id <> requested.provision_id
        ), '[]'::jsonb) AS descendants,
        COALESCE((
            SELECT jsonb_agg(
                ancestor.summary ORDER BY ancestor.depth
            )
            FROM ancestors AS ancestor
        ), '[]'::jsonb) AS ancestors,
        jsonb_build_object(
            'previous', (
                SELECT adjacent.summary
                FROM provision_rows AS adjacent
                WHERE adjacent.release_tag = requested.release_tag
                  AND adjacent.act_id = requested.act_id
                  AND adjacent.sequence = requested.sequence - 1
            ),
            'next', (
                SELECT adjacent.summary
                FROM provision_rows AS adjacent
                WHERE adjacent.release_tag = requested.release_tag
                  AND adjacent.act_id = requested.act_id
                  AND adjacent.sequence = requested.sequence + 1
            )
        ) AS navigation,
        COALESCE((
            SELECT jsonb_agg(source.canonical_record ORDER BY source.source_id)
            FROM sources AS source
            WHERE source.release_tag = requested.release_tag
              AND source.source_id IN (SELECT source_id FROM referenced_source_ids)
        ), '[]'::jsonb) AS sources,
        (SELECT count(*) FROM referenced_source_ids) AS source_count,
        COALESCE((
            SELECT jsonb_agg(
                jsonb_build_object(
                    'citation', citation.canonical_record,
                    'target', jsonb_build_object(
                        'act', target_act.summary,
                        'provision', target_provision.summary
                    )
                ) ORDER BY origin.sequence, citation.citation_id
            )
            FROM citations AS citation
            JOIN subtree AS origin
              ON origin.release_tag = citation.release_tag
             AND origin.provision_id = citation.source_provision_id
            JOIN act_rows AS target_act
              ON target_act.release_tag = citation.release_tag
             AND target_act.act_id = citation.target_act_id
            LEFT JOIN provision_rows AS target_provision
              ON target_provision.release_tag = citation.release_tag
             AND target_provision.act_id = citation.target_act_id
             AND target_provision.provision_id = citation.target_provision_id
        ), '[]'::jsonb) AS citations
    FROM requested
    JOIN act_rows AS act
      ON act.release_tag = requested.release_tag
     AND act.act_id = requested.act_id
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

_SEARCH_EXACT_ACTS_SQL = """
    SELECT
        act.act_id,
        act.official_title,
        act.short_title,
        act.year,
        act.number,
        act.citation,
        act.text_kind,
        act.status,
        act.checked_through_date,
        act.canonical_record AS act_record
    FROM acts AS act
    WHERE act.release_tag = %s
      AND act.act_id = COALESCE(%s, act.act_id)
      AND (
          act.act_id = %s
          OR act.citation_key = %s
          OR act.title_keys @> ARRAY[%s]
      )
    ORDER BY act.act_id
"""

_SEARCH_EXACT_PROVISIONS_SQL = """
    SELECT
        act.act_id,
        act.official_title,
        act.short_title,
        act.year,
        act.number,
        act.citation,
        act.text_kind,
        act.status,
        act.checked_through_date,
        provision.provision_id,
        provision.node_type,
        provision.display_label,
        provision.heading,
        CASE
            WHEN char_length(provision.searchable_text)
               > char_length(provision.reference_key)
            THEN left(
                provision.searchable_text,
                LEAST(
                    320,
                    char_length(provision.searchable_text)
                        - char_length(provision.reference_key) - 1
                )
            )
            ELSE NULL
        END AS excerpt
    FROM provisions AS provision
    JOIN acts AS act
      ON act.release_tag = provision.release_tag
     AND act.act_id = provision.act_id
    WHERE provision.release_tag = %s
      AND provision.act_id = COALESCE(%s, provision.act_id)
      AND (
          provision.provision_id = %s
          OR (
              provision.act_id = %s
              AND provision.reference_key = %s
          )
      )
    ORDER BY provision.act_id, provision.sequence
"""

_SEARCH_NAMED_ACTS_SQL = """
    SELECT act_id, title_keys
    FROM acts AS act
    WHERE release_tag = %s
      AND EXISTS (
          SELECT 1
          FROM unnest(act.title_keys) AS title_key
          WHERE strpos(%s, ' ' || title_key || ' ') > 0
      )
    ORDER BY act_id
    LIMIT 2
"""

_SEARCH_LEXICAL_SQL = """
    WITH search_query AS (
        SELECT websearch_to_tsquery('english', %s) AS value
    ),
    matches AS (
        SELECT
            'act'::text AS kind,
            ts_rank_cd(act.search_vector, search_query.value) AS rank,
            act.act_id,
            0 AS sequence,
            act.official_title,
            act.short_title,
            act.year,
            act.number,
            act.citation,
            act.text_kind,
            act.status,
            act.checked_through_date,
            NULL::text AS provision_id,
            NULL::text AS node_type,
            NULL::text AS display_label,
            NULL::text AS heading,
            NULL::text AS excerpt
        FROM acts AS act
        CROSS JOIN search_query
        WHERE act.release_tag = %s
          AND act.act_id = COALESCE(%s, act.act_id)
          AND act.search_vector @@ search_query.value

        UNION ALL

        SELECT
            'provision'::text AS kind,
            ts_rank_cd(provision.search_vector, search_query.value) AS rank,
            provision.act_id,
            provision.sequence,
            act.official_title,
            act.short_title,
            act.year,
            act.number,
            act.citation,
            act.text_kind,
            act.status,
            act.checked_through_date,
            provision.provision_id,
            provision.node_type,
            provision.display_label,
            provision.heading,
            CASE
                WHEN char_length(provision.searchable_text)
                   > char_length(provision.reference_key)
                THEN left(
                    provision.searchable_text,
                    LEAST(
                        320,
                        char_length(provision.searchable_text)
                            - char_length(provision.reference_key) - 1
                    )
                )
                ELSE NULL
            END AS excerpt
        FROM provisions AS provision
        JOIN acts AS act
          ON act.release_tag = provision.release_tag
         AND act.act_id = provision.act_id
        CROSS JOIN search_query
        WHERE provision.release_tag = %s
          AND provision.act_id = COALESCE(%s, provision.act_id)
          AND provision.search_vector @@ search_query.value
    )
    SELECT *
    FROM matches
    ORDER BY rank DESC, act_id, sequence
    LIMIT %s
"""

_SEARCH_BREADCRUMBS_SQL = """
    WITH RECURSIVE ancestors AS (
        SELECT
            requested.provision_id AS requested_id,
            parent.release_tag,
            parent.provision_id,
            parent.act_id,
            parent.parent_provision_id,
            parent.depth,
            parent.node_type,
            parent.display_label,
            parent.heading
        FROM provisions AS requested
        JOIN provisions AS parent
          ON parent.release_tag = requested.release_tag
         AND parent.act_id = requested.act_id
         AND parent.provision_id = requested.parent_provision_id
        WHERE requested.release_tag = %s
          AND requested.provision_id = ANY(%s)

        UNION ALL

        SELECT
            child.requested_id,
            parent.release_tag,
            parent.provision_id,
            parent.act_id,
            parent.parent_provision_id,
            parent.depth,
            parent.node_type,
            parent.display_label,
            parent.heading
        FROM ancestors AS child
        JOIN provisions AS parent
          ON parent.release_tag = child.release_tag
         AND parent.act_id = child.act_id
         AND parent.provision_id = child.parent_provision_id
    )
    SELECT
        requested_id,
        jsonb_agg(
            jsonb_build_object(
                'provision_id', provision_id,
                'act_id', act_id,
                'node_type', node_type,
                'display_label', display_label,
                'heading', heading
            ) ORDER BY depth
        ) AS breadcrumb
    FROM ancestors
    GROUP BY requested_id
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

    def get_act_contents(
        self, release_tag: str, act_id: str
    ) -> list[dict[str, Any]] | None: ...

    def get_provision(
        self, release_tag: str, provision_id: str
    ) -> dict[str, Any] | None: ...

    def get_source(self, release_tag: str, source_id: str) -> dict[str, Any] | None: ...

    def search(
        self,
        release_tag: str,
        query: str,
        act_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]] | None: ...


def _ordered_source_ids(act: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(ref["source_id"] for ref in act["source_refs"]))


def _normalize_search_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized))


def _act_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "act_id": row["act_id"],
        "official_title": row["official_title"],
        "short_title": row["short_title"],
        "year": row["year"],
        "number": row["number"],
        "citation": row["citation"],
        "text_kind": row["text_kind"],
        "status": row["status"],
        "checked_through_date": row["checked_through_date"],
    }


def _provision_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provision_id": row["provision_id"],
        "act_id": row["act_id"],
        "node_type": row["node_type"],
        "display_label": row["display_label"],
        "heading": row["heading"],
    }


def _act_match_kind(row: dict[str, Any], query: str, match_key: str) -> str | None:
    if row["act_id"] == query:
        return "exact_act_id"
    record = row["act_record"]
    if record["citation"] is not None and (
        _normalize_search_key(record["citation"]) == match_key
    ):
        return "exact_act_citation"
    titles = record["titles"]
    if any(
        value is not None and _normalize_search_key(value) == match_key
        for value in (titles["official"], titles["short"])
    ):
        return "exact_act_title"
    if any(_normalize_search_key(alias) == match_key for alias in record["aliases"]):
        return "exact_act_alias"
    return None


def _act_search_item(row: dict[str, Any], match_kind: str) -> dict[str, Any]:
    return {
        "kind": "act",
        "match_kind": match_kind,
        "act": _act_summary(row),
        "provision": None,
        "breadcrumb": [],
        "excerpt": None,
    }


def _provision_search_item(row: dict[str, Any], match_kind: str) -> dict[str, Any]:
    return {
        "kind": "provision",
        "match_kind": match_kind,
        "act": _act_summary(row),
        "provision": _provision_summary(row),
        "breadcrumb": [],
        "excerpt": row["excerpt"],
    }


def _search_item_key(item: dict[str, Any]) -> tuple[str, str]:
    provision = item["provision"]
    return (
        ("provision", provision["provision_id"])
        if provision is not None
        else ("act", item["act"]["act_id"])
    )


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

    def get_act_contents(
        self, release_tag: str, act_id: str
    ) -> list[dict[str, Any]] | None:
        with self._connection() as connection:
            act = connection.execute(
                "SELECT 1 FROM acts WHERE release_tag = %s AND act_id = %s",
                (release_tag, act_id),
            ).fetchone()
            if act is None:
                return None
            rows = connection.execute(
                _GET_ACT_CONTENTS_SQL,
                (release_tag, act_id),
            ).fetchall()
        return list(rows)

    def get_provision(
        self, release_tag: str, provision_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                _GET_PROVISION_SQL,
                (release_tag, release_tag, provision_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if len(result["sources"]) != result.pop("source_count"):
            raise ProjectionUnavailable("provision source projection is incomplete")
        return result

    def get_source(self, release_tag: str, source_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                _GET_SOURCE_SQL,
                (release_tag, source_id),
            ).fetchone()
        return None if row is None else row["canonical_record"]

    def search(
        self,
        release_tag: str,
        query: str,
        act_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        match_key = _normalize_search_key(query)
        reference_act_id = act_id or ""
        reference_key = match_key if act_id is not None else ""

        with self._connection() as connection:
            if act_id is not None:
                act_exists = connection.execute(
                    "SELECT 1 FROM acts WHERE release_tag = %s AND act_id = %s",
                    (release_tag, act_id),
                ).fetchone()
                if act_exists is None:
                    return None
            elif match_key:
                named_acts = connection.execute(
                    _SEARCH_NAMED_ACTS_SQL,
                    (release_tag, f" {match_key} "),
                ).fetchall()
                if len(named_acts) == 1:
                    title_keys = [
                        key
                        for key in named_acts[0]["title_keys"]
                        if f" {key} " in f" {match_key} "
                    ]
                    if title_keys:
                        name_key = max(title_keys, key=len)
                        padded_key = f" {match_key} "
                        start = padded_key.index(f" {name_key} ")
                        reference_key = " ".join(
                            (
                                padded_key[:start]
                                + padded_key[start + len(name_key) + 2 :]
                            ).split()
                        )
                        reference_act_id = named_acts[0]["act_id"]

            exact_act_rows = connection.execute(
                _SEARCH_EXACT_ACTS_SQL,
                (release_tag, act_id, query, match_key, match_key),
            ).fetchall()
            exact_provision_rows = connection.execute(
                _SEARCH_EXACT_PROVISIONS_SQL,
                (
                    release_tag,
                    act_id,
                    query,
                    reference_act_id,
                    reference_key,
                ),
            ).fetchall()
            lexical_rows = connection.execute(
                _SEARCH_LEXICAL_SQL,
                (query, release_tag, act_id, release_tag, act_id, limit),
            ).fetchall()

            exact_items = []
            for row in exact_act_rows:
                match_kind = _act_match_kind(row, query, match_key)
                if match_kind is not None:
                    exact_items.append(_act_search_item(row, match_kind))
            for row in exact_provision_rows:
                match_kind = (
                    "exact_provision_id"
                    if row["provision_id"] == query
                    else "exact_provision_reference"
                )
                exact_items.append(_provision_search_item(row, match_kind))

            priority = {
                "exact_act_id": 1,
                "exact_provision_id": 1,
                "exact_act_citation": 2,
                "exact_act_title": 3,
                "exact_act_alias": 4,
                "exact_provision_reference": 5,
            }
            exact_items.sort(
                key=lambda item: (
                    priority[item["match_kind"]],
                    _search_item_key(item)[1],
                )
            )
            lexical_items = [
                (
                    _act_search_item(row, "lexical")
                    if row["kind"] == "act"
                    else _provision_search_item(row, "lexical")
                )
                for row in lexical_rows
            ]

            items = []
            seen: set[tuple[str, str]] = set()
            for item in [*exact_items, *lexical_items]:
                key = _search_item_key(item)
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
                if len(items) == limit:
                    break

            provision_ids = [
                item["provision"]["provision_id"]
                for item in items
                if item["provision"] is not None
            ]
            breadcrumbs = {provision_id: [] for provision_id in provision_ids}
            if provision_ids:
                breadcrumb_rows = connection.execute(
                    _SEARCH_BREADCRUMBS_SQL,
                    (release_tag, provision_ids),
                ).fetchall()
                breadcrumbs.update(
                    {row["requested_id"]: row["breadcrumb"] for row in breadcrumb_rows}
                )
            for item in items:
                if item["provision"] is not None:
                    item["breadcrumb"] = breadcrumbs[item["provision"]["provision_id"]]

        return items
