"""Build a deterministic PostgreSQL projection from an exact corpus tag."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import ProjectionSettings
from openacts_pipeline.corpus import load_corpus
from openacts_pipeline.corpus_files import CorpusRecords

RELEASE_TAG_PATTERN = re.compile(r"corpus-v[0-9]+\.[0-9]+\.[0-9]+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
PROJECTION_SCHEMA_VERSION = 1
SUPPORTED_CANONICAL_SCHEMA_VERSIONS = {"0.1.0"}
TEXT_BLOCK_KINDS = {"text", "quoted_text", "formula", "signature"}
PROJECTION_ADVISORY_LOCK_ID = int.from_bytes(b"OPENACTS", "big")
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PROBES = (
    """
    SELECT release_tag, commit_sha, canonical_schema_versions,
           projection_schema_version, import_state, imported_at
    FROM corpus_releases LIMIT 0
    """,
    """
    SELECT singleton, active_release_tag, previous_release_tag, activated_at
    FROM projection_state LIMIT 0
    """,
    """
    SELECT release_tag, source_id, schema_version, document_title,
           document_publisher, language, source_class, canonical_record
    FROM sources LIMIT 0
    """,
    """
    SELECT release_tag, act_id, schema_version, jurisdiction, country_code,
           official_title, short_title, year, number, citation, text_kind,
           status, checked_through_date, title_keys, citation_key, source_ids,
           searchable_text, search_vector, canonical_record
    FROM acts LIMIT 0
    """,
    """
    SELECT release_tag, provision_id, act_id, schema_version,
           parent_provision_id, sibling_order, sequence, depth, node_type,
           display_label, heading, text_fidelity, reference_key, source_ids,
           searchable_text, search_vector, canonical_record
    FROM provisions LIMIT 0
    """,
    """
    SELECT release_tag, citation_id, schema_version, source_provision_id,
           source_block_id, target_act_id, target_provision_id,
           canonical_record
    FROM citations LIMIT 0
    """,
)
PROJECTION_STATE_QUERY = """
    SELECT
        state.active_release_tag,
        state.previous_release_tag,
        active.commit_sha AS active_commit_sha,
        active.canonical_schema_versions AS active_canonical_schema_versions,
        active.projection_schema_version AS active_projection_schema_version,
        active.import_state AS active_import_state,
        previous.commit_sha AS previous_commit_sha,
        previous.canonical_schema_versions AS previous_canonical_schema_versions,
        previous.projection_schema_version AS previous_projection_schema_version,
        previous.import_state AS previous_import_state,
        target.release_tag AS target_release_tag,
        target.commit_sha AS target_commit_sha,
        target.canonical_schema_versions AS target_canonical_schema_versions,
        target.projection_schema_version AS target_projection_schema_version,
        target.import_state AS target_import_state
    FROM projection_state AS state
    LEFT JOIN corpus_releases AS active
      ON active.release_tag = state.active_release_tag
    LEFT JOIN corpus_releases AS previous
      ON previous.release_tag = state.previous_release_tag
    LEFT JOIN corpus_releases AS target
      ON target.release_tag = %s
    WHERE state.singleton
"""


@dataclass(frozen=True, slots=True)
class TaggedCorpus:
    release_tag: str
    commit_sha: str
    records: CorpusRecords

    @property
    def schema_versions(self) -> tuple[str, ...]:
        return self.records.schema_versions


@dataclass(frozen=True, slots=True)
class SourceRow:
    source_id: str
    schema_version: str
    document_title: str
    document_publisher: str
    language: str
    source_class: str
    canonical_record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActRow:
    act_id: str
    schema_version: str
    jurisdiction: str
    country_code: str
    official_title: str
    short_title: str | None
    year: int
    number: str | None
    citation: str | None
    text_kind: str
    status: str
    checked_through_date: date | None
    title_keys: tuple[str, ...]
    citation_key: str | None
    source_ids: tuple[str, ...]
    searchable_text: str
    canonical_record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProvisionRow:
    provision_id: str
    act_id: str
    schema_version: str
    parent_provision_id: str | None
    sibling_order: int
    sequence: int
    depth: int
    node_type: str
    display_label: str | None
    heading: str | None
    text_fidelity: str
    reference_key: str
    source_ids: tuple[str, ...]
    searchable_text: str
    canonical_record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CitationRow:
    citation_id: str
    schema_version: str
    source_provision_id: str
    source_block_id: str
    target_act_id: str
    target_provision_id: str | None
    canonical_record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProjectionRows:
    release_tag: str
    commit_sha: str
    canonical_schema_versions: tuple[str, ...]
    projection_schema_version: int
    sources: tuple[SourceRow, ...]
    acts: tuple[ActRow, ...]
    provisions: tuple[ProvisionRow, ...]
    citations: tuple[CitationRow, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "acts": len(self.acts),
            "sources": len(self.sources),
            "provisions": len(self.provisions),
            "citations": len(self.citations),
        }


@dataclass(frozen=True, slots=True)
class ProjectionBlocker:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class DatabaseRelease:
    release_tag: str
    commit_sha: str
    canonical_schema_versions: tuple[str, ...]
    projection_schema_version: int
    import_state: str


@dataclass(frozen=True, slots=True)
class DatabaseProjectionState:
    server_major_version: int
    transaction_read_only: bool
    active_release: DatabaseRelease | None
    previous_release: DatabaseRelease | None
    target_release: DatabaseRelease | None
    blockers: tuple[ProjectionBlocker, ...] = ()


def _blocked_database_state(
    server_major_version: int, transaction_read_only: bool, code: str, message: str
) -> DatabaseProjectionState:
    return DatabaseProjectionState(
        server_major_version, transaction_read_only, None, None, None,
        (ProjectionBlocker(code, message),),
    )


def _release_dict(release: DatabaseRelease | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {
        "tag": release.release_tag,
        "commit_sha": release.commit_sha,
        "canonical_schema_versions": list(release.canonical_schema_versions),
        "projection_schema_version": release.projection_schema_version,
        "import_state": release.import_state,
    }


def _pointed_release_blockers(
    role: str, release: DatabaseRelease | None
) -> list[ProjectionBlocker]:
    if release is None:
        return []
    if (
        release.import_state != "ready"
        or release.projection_schema_version != PROJECTION_SCHEMA_VERSION
        or set(release.canonical_schema_versions) - SUPPORTED_CANONICAL_SCHEMA_VERSIONS
    ):
        return [
            ProjectionBlocker(
                code=f"{role}_release_invalid",
                message=f"{role} release metadata is incomplete or unsupported",
            )
        ]
    return []


def _projection_action(
    rows: ProjectionRows,
    database: DatabaseProjectionState,
    *,
    expected_read_only: bool,
) -> tuple[str, tuple[ProjectionBlocker, ...]]:
    blockers = list(database.blockers)
    if database.server_major_version != 17:
        blockers.append(
            ProjectionBlocker(
                code="unsupported_postgres_version",
                message="projection tooling is verified against PostgreSQL 17",
            )
        )
    if database.transaction_read_only != expected_read_only:
        blockers.append(
            ProjectionBlocker(
                code=(
                    "projection_not_read_only"
                    if expected_read_only
                    else "projection_database_read_only"
                ),
                message=(
                    "database inspection transaction is not read-only"
                    if expected_read_only
                    else "projection execution transaction is read-only"
                ),
            )
        )
    blockers.extend(_pointed_release_blockers("active", database.active_release))
    blockers.extend(_pointed_release_blockers("previous", database.previous_release))

    target = database.target_release
    if target is not None:
        if target.commit_sha != rows.commit_sha:
            blockers.append(
                ProjectionBlocker(
                    code="release_commit_conflict",
                    message="release tag exists with a different commit",
                )
            )
        elif (
            target.canonical_schema_versions != rows.canonical_schema_versions
            or target.projection_schema_version != rows.projection_schema_version
        ):
            blockers.append(
                ProjectionBlocker(
                    code="release_metadata_conflict",
                    message="stored release schema metadata differs from the tag",
                )
            )
        elif target.import_state != "ready":
            blockers.append(
                ProjectionBlocker(
                    code="release_import_incomplete",
                    message="stored release import is incomplete",
                )
            )

    if blockers:
        return "blocked", tuple(blockers)
    if target is None:
        return "import_and_activate", ()
    if (
        database.active_release is not None
        and database.active_release.release_tag == rows.release_tag
    ):
        return "noop", ()
    return "activate_existing", ()


def _projection_result(
    rows: ProjectionRows,
    database: DatabaseProjectionState,
    *,
    action: str,
    blockers: tuple[ProjectionBlocker, ...],
    execute: bool,
    writes_performed: bool,
) -> dict[str, Any]:
    bootstrap_release = rows.release_tag == "corpus-v0.0.0"
    warnings = (
        [
            {
                "code": "bootstrap_release",
                "message": (
                    "corpus-v0.0.0 is for release-tooling exercises, not "
                    "production activation"
                ),
            }
        ]
        if bootstrap_release
        else []
    )
    return {
        "stage": "projection",
        "status": "blocked" if blockers else "success" if execute else "ready",
        "execute": execute,
        "network_access": True,
        "writes_performed": writes_performed,
        "transaction_read_only": database.transaction_read_only,
        "release": {
            "tag": rows.release_tag,
            "commit_sha": rows.commit_sha,
            "bootstrap_release": bootstrap_release,
            "canonical_schema_versions": list(rows.canonical_schema_versions),
            "projection_schema_version": rows.projection_schema_version,
            "counts": rows.counts,
        },
        "database": {
            "server_major_version": database.server_major_version,
            "active_release_tag": (
                database.active_release.release_tag
                if database.active_release is not None
                else None
            ),
            "previous_release_tag": (
                database.previous_release.release_tag
                if database.previous_release is not None
                else None
            ),
            "target_release": _release_dict(database.target_release),
        },
        "action": action,
        "blockers": [blocker.as_dict() for blocker in blockers],
        "warnings": warnings,
    }


def build_projection_plan(
    rows: ProjectionRows, database: DatabaseProjectionState
) -> dict[str, Any]:
    """Choose the next projection action from validated rows and database state."""
    action, blockers = _projection_action(rows, database, expected_read_only=True)
    return _projection_result(
        rows,
        database,
        action=action,
        blockers=blockers,
        execute=False,
        writes_performed=False,
    )


def _database_release(row: dict[str, Any], prefix: str) -> DatabaseRelease | None:
    release_tag = row[f"{prefix}_release_tag"]
    if release_tag is None:
        return None
    try:
        return DatabaseRelease(
            release_tag=release_tag,
            commit_sha=row[f"{prefix}_commit_sha"],
            canonical_schema_versions=tuple(row[f"{prefix}_canonical_schema_versions"]),
            projection_schema_version=row[f"{prefix}_projection_schema_version"],
            import_state=row[f"{prefix}_import_state"],
        )
    except (KeyError, TypeError) as exc:
        raise PipelineError(
            "invalid_projection_state",
            f"{prefix} release metadata is invalid",
        ) from exc


def _projection_database_state(
    connection: psycopg.Connection[dict[str, Any]],
    release_tag: str,
    *,
    lock_state: bool = False,
) -> DatabaseProjectionState:
    transaction_row = connection.execute("SHOW transaction_read_only").fetchone()
    version_row = connection.execute("SHOW server_version_num").fetchone()
    if transaction_row is None or version_row is None:
        raise PipelineError(
            "invalid_projection_state",
            "database did not report transaction or server state",
        )
    transaction_read_only = transaction_row["transaction_read_only"] == "on"
    server_major_version = int(version_row["server_version_num"]) // 10000
    try:
        for query in SCHEMA_PROBES:
            connection.execute(query)
        if lock_state:
            state_exists = connection.execute(
                "SELECT singleton FROM projection_state WHERE singleton FOR UPDATE"
            ).fetchone()
            if state_exists is None:
                return _blocked_database_state(
                    server_major_version,
                    transaction_read_only,
                    "projection_state_missing",
                    "projection_state singleton row is missing",
                )
        state_row = connection.execute(PROJECTION_STATE_QUERY, (release_tag,)).fetchone()
    except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable):
        return _blocked_database_state(
            server_major_version,
            transaction_read_only,
            "projection_schema_unavailable",
            "required projection tables or columns are missing",
        )
    if state_row is None:
        return _blocked_database_state(
            server_major_version,
            transaction_read_only,
            "projection_state_missing",
            "projection_state singleton row is missing",
        )
    return DatabaseProjectionState(
        server_major_version=server_major_version,
        transaction_read_only=transaction_read_only,
        active_release=_database_release(state_row, "active"),
        previous_release=_database_release(state_row, "previous"),
        target_release=_database_release(state_row, "target"),
    )


def inspect_projection_database(
    database_url: str, release_tag: str
) -> DatabaseProjectionState:
    """Read one compatible projection-state snapshot without permitting writes."""
    try:
        with psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            connection.read_only = True
            with connection.transaction():
                return _projection_database_state(connection, release_tag)
    except psycopg.OperationalError as exc:
        raise PipelineError(
            "projection_database_unavailable",
            "cannot connect to the projection database",
            retryable=True,
        ) from exc
    except psycopg.Error as exc:
        raise PipelineError(
            "projection_database_error",
            "cannot inspect the projection database",
        ) from exc


def preview_projection(
    repo_root: Path,
    release_tag: str,
    *,
    settings: ProjectionSettings | None = None,
) -> dict[str, Any]:
    """Validate a tagged release and report its non-writing database action."""
    rows = build_projection_rows(load_tagged_corpus(repo_root, release_tag))
    active_settings = settings or ProjectionSettings.from_env()
    database = inspect_projection_database(
        active_settings.database_url, rows.release_tag
    )
    return build_projection_plan(rows, database)


def _insert_projection_records(
    connection: psycopg.Connection[dict[str, Any]],
    table: str,
    release_tag: str,
    records: tuple[Any, ...],
) -> None:
    if not records:
        return
    column_names = ("release_tag", *(field.name for field in fields(records[0])))
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, column_names)),
        sql.SQL(", ").join(sql.Placeholder() for _column in column_names),
    )

    def values(record: Any) -> tuple[Any, ...]:
        projected = []
        for field in fields(record):
            value = getattr(record, field.name)
            if field.name == "canonical_record":
                value = Jsonb(value)
            elif isinstance(value, tuple):
                value = list(value)
            projected.append(value)
        return (release_tag, *projected)

    with connection.cursor() as cursor:
        cursor.executemany(statement, (values(record) for record in records))


def _import_projection_rows(
    connection: psycopg.Connection[dict[str, Any]], rows: ProjectionRows
) -> None:
    connection.execute(
        """
        INSERT INTO corpus_releases (
            release_tag, commit_sha, canonical_schema_versions,
            projection_schema_version, import_state
        ) VALUES (%s, %s, %s, %s, 'importing')
        """,
        (
            rows.release_tag,
            rows.commit_sha,
            list(rows.canonical_schema_versions),
            rows.projection_schema_version,
        ),
    )
    for table, records in (
        ("sources", rows.sources),
        ("acts", rows.acts),
        ("provisions", rows.provisions),
        ("citations", rows.citations),
    ):
        _insert_projection_records(connection, table, rows.release_tag, records)
    actual = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM acts WHERE release_tag = %s) AS acts,
            (SELECT count(*) FROM sources WHERE release_tag = %s) AS sources,
            (SELECT count(*) FROM provisions WHERE release_tag = %s) AS provisions,
            (SELECT count(*) FROM citations WHERE release_tag = %s) AS citations
        """,
        (rows.release_tag,) * 4,
    ).fetchone()
    if actual is None or actual != rows.counts:
        raise PipelineError(
            "projection_import_failed",
            "imported row counts do not match the tagged corpus",
        )
    connection.execute(
        """
        UPDATE corpus_releases SET import_state = 'ready'
        WHERE release_tag = %s AND import_state = 'importing'
        """,
        (rows.release_tag,),
    )


def execute_projection(
    repo_root: Path,
    release_tag: str,
    *,
    settings: ProjectionSettings | None = None,
    allow_bootstrap: bool = False,
) -> dict[str, Any]:
    """Import and activate one exact tagged release in a single transaction."""
    rows = build_projection_rows(load_tagged_corpus(repo_root, release_tag))
    if rows.release_tag == "corpus-v0.0.0" and not allow_bootstrap:
        raise PipelineError(
            "bootstrap_release_blocked",
            "corpus-v0.0.0 execution requires explicit bootstrap approval",
        )
    active_settings = settings or ProjectionSettings.from_env()
    try:
        with psycopg.connect(
            active_settings.database_url,
            autocommit=True,
            row_factory=dict_row,
        ) as connection, connection.transaction():
            read_only = connection.execute("SHOW transaction_read_only").fetchone()
            if read_only is None or read_only["transaction_read_only"] == "on":
                raise PipelineError(
                    "projection_database_read_only",
                    "projection execution transaction is read-only",
                )
            lock = connection.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS acquired",
                (PROJECTION_ADVISORY_LOCK_ID,),
            ).fetchone()
            if lock is None or not lock["acquired"]:
                raise PipelineError(
                    "projection_busy",
                    "another projection operation is in progress",
                    retryable=True,
                )
            database = _projection_database_state(
                connection, rows.release_tag, lock_state=True
            )
            action, blockers = _projection_action(rows, database, expected_read_only=False)
            if blockers:
                raise PipelineError(blockers[0].code, blockers[0].message)
            if action == "import_and_activate":
                _import_projection_rows(connection, rows)
            if action != "noop":
                connection.execute(
                    """
                    UPDATE projection_state
                    SET previous_release_tag = active_release_tag,
                        active_release_tag = %s,
                        activated_at = CURRENT_TIMESTAMP
                    WHERE singleton
                    """,
                    (rows.release_tag,),
                )
            final_database = _projection_database_state(connection, rows.release_tag)
            return _projection_result(
                rows,
                final_database,
                action=action,
                blockers=(),
                execute=True,
                writes_performed=action != "noop",
            )
    except PipelineError:
        raise
    except psycopg.OperationalError as exc:
        raise PipelineError(
            "projection_database_unavailable",
            "cannot connect to the projection database",
            retryable=True,
        ) from exc
    except (psycopg.IntegrityError, psycopg.DataError) as exc:
        raise PipelineError(
            "projection_import_failed",
            "database rejected the tagged corpus projection",
        ) from exc
    except psycopg.Error as exc:
        raise PipelineError(
            "projection_database_error",
            "cannot execute the corpus projection",
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openacts-projection")
    parser.add_argument("release")
    parser.add_argument(
        "--execute", action="store_true", help="import and activate the release"
    )
    parser.add_argument(
        "--allow-bootstrap",
        action="store_true",
        help="permit local execution of non-production corpus-v0.0.0",
    )
    args = parser.parse_args(argv)
    if args.allow_bootstrap and not args.execute:
        parser.error("--allow-bootstrap requires --execute")
    try:
        result = (
            execute_projection(
                REPO_ROOT,
                args.release,
                allow_bootstrap=args.allow_bootstrap,
            )
            if args.execute
            else preview_projection(REPO_ROOT, args.release)
        )
    except PipelineError as exc:
        print(
            json.dumps({"status": "failure", "error": exc.as_dict()}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _git(repo_root: Path, *arguments: str, code: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise PipelineError(code, f"cannot run Git: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PipelineError(code, detail or "Git command failed")
    return completed.stdout.strip()


def load_tagged_corpus(repo_root: Path, release_tag: str) -> TaggedCorpus:
    """Load corpus data and schemas from the commit behind an exact local tag."""
    if RELEASE_TAG_PATTERN.fullmatch(release_tag) is None:
        raise PipelineError("invalid_release_tag", "release must match corpus-vX.Y.Z")
    commit_sha = _git(
        repo_root,
        "rev-parse",
        "--verify",
        f"refs/tags/{release_tag}^{{commit}}",
        code="release_tag_not_found",
    )
    if COMMIT_PATTERN.fullmatch(commit_sha) is None:
        raise PipelineError(
            "invalid_release_tag", "release tag did not resolve to a full commit"
        )

    with tempfile.TemporaryDirectory(prefix="openacts-release-") as temporary_name:
        temporary = Path(temporary_name)
        archive_path = temporary / "release.tar"
        _git(
            repo_root,
            "archive",
            "--format=tar",
            f"--output={archive_path}",
            commit_sha,
            "--",
            "corpus",
            "schemas",
            code="release_archive_failed",
        )
        try:
            with tarfile.open(archive_path) as archive:
                archive.extractall(temporary, filter="data")
        except (OSError, tarfile.TarError) as exc:
            raise PipelineError(
                "release_archive_failed", f"cannot extract release: {exc}"
            ) from exc
        records = load_corpus(temporary / "corpus", schema_dir=temporary / "schemas")
    return TaggedCorpus(
        release_tag=release_tag,
        commit_sha=commit_sha,
        records=records,
    )


def _normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized))


def _unique_keys(values: list[str | None]) -> tuple[str, ...]:
    keys: list[str] = []
    for value in values:
        if value is None:
            continue
        key = _normalize_key(value)
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)


def _join_text(values: list[str | None]) -> str:
    return "\n".join(value.strip() for value in values if value and value.strip())


def _content_text(blocks: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    for block in blocks:
        kind = block["kind"]
        if kind in TEXT_BLOCK_KINDS:
            parts.append(block["text"])
        elif kind == "list":
            for item in block["items"]:
                if item["label"] is not None:
                    parts.append(item["label"])
                parts.extend(_content_text(item["content_blocks"]))
        elif kind == "table":
            if block["caption"] is not None:
                parts.append(block["caption"]["text"])
            for group in block["row_groups"]:
                for row in group["rows"]:
                    for cell in row["cells"]:
                        parts.extend(_content_text(cell["content_blocks"]))
            parts.extend(_content_text(block["notes"]))
    return parts


def _source_ids(value: object) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            spans = item.get("source_spans")
            if isinstance(spans, list):
                found.update(
                    span["source_id"]
                    for span in spans
                    if isinstance(span, dict) and isinstance(span.get("source_id"), str)
                )
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


def _source_rows(records: CorpusRecords) -> tuple[SourceRow, ...]:
    return tuple(
        SourceRow(
            source_id=source["source_id"],
            schema_version=source["schema_version"],
            document_title=source["document_title"],
            document_publisher=source["document_publisher"],
            language=source["language"],
            source_class=source["source_class"],
            canonical_record=source,
        )
        for source in sorted(records.sources, key=lambda record: record["source_id"])
    )


def _act_row(act: dict[str, Any]) -> ActRow:
    titles = act["titles"]
    title_values = [
        titles["official"],
        titles["short"],
        titles["long"],
        *act["aliases"],
    ]
    return ActRow(
        act_id=act["act_id"],
        schema_version=act["schema_version"],
        jurisdiction=act["jurisdiction"],
        country_code=act["country_code"],
        official_title=titles["official"],
        short_title=titles["short"],
        year=act["year"],
        number=act["number"],
        citation=act["citation"],
        text_kind=act.get("text_kind", "as_enacted"),
        status=act["status"],
        checked_through_date=_optional_date(act["checked_through_date"]),
        title_keys=_unique_keys(title_values),
        citation_key=(
            _normalize_key(act["citation"]) if act["citation"] is not None else None
        ),
        source_ids=tuple(
            sorted({relation["source_id"] for relation in act["source_refs"]})
        ),
        searchable_text=_join_text(
            [*title_values, act["citation"], str(act["year"]), act["number"]]
        ),
        canonical_record=act,
    )


def _provision_rows(
    act: dict[str, Any], provisions: tuple[dict[str, Any], ...]
) -> tuple[ProvisionRow, ...]:
    rows: list[ProvisionRow] = []
    depths: dict[str, int] = {}
    reference_keys: set[str] = set()
    for sequence, provision in enumerate(provisions, start=1):
        parent_id = provision["parent_provision_id"]
        depth = 0 if parent_id is None else depths[parent_id] + 1
        depths[provision["provision_id"]] = depth
        local_id = provision["provision_id"].split(":", 1)[1]
        reference_key = _normalize_key(local_id)
        if reference_key in reference_keys:
            raise PipelineError(
                "invalid_projection",
                f"duplicate Provision reference key in {act['act_id']}: "
                f"{reference_key}",
            )
        reference_keys.add(reference_key)
        searchable_text = _join_text(
            [
                provision["display_label"],
                provision["heading"],
                *_content_text(provision["content_blocks"]),
                reference_key,
            ]
        )
        rows.append(
            ProvisionRow(
                provision_id=provision["provision_id"],
                act_id=act["act_id"],
                schema_version=provision["schema_version"],
                parent_provision_id=parent_id,
                sibling_order=provision["order"],
                sequence=sequence,
                depth=depth,
                node_type=provision["node_type"],
                display_label=provision["display_label"],
                heading=provision["heading"],
                text_fidelity=provision["text_fidelity"],
                reference_key=reference_key,
                source_ids=_source_ids(provision),
                searchable_text=searchable_text,
                canonical_record=provision,
            )
        )
    return tuple(rows)


def _citation_rows(records: CorpusRecords) -> tuple[CitationRow, ...]:
    return tuple(
        CitationRow(
            citation_id=citation["citation_id"],
            schema_version=citation["schema_version"],
            source_provision_id=citation["source_provision_id"],
            source_block_id=citation["source_block_id"],
            target_act_id=citation["target"]["act_id"],
            target_provision_id=citation["target"]["provision_id"],
            canonical_record=citation,
        )
        for citation in sorted(
            records.citations, key=lambda record: record["citation_id"]
        )
    )


def build_projection_rows(tagged: TaggedCorpus) -> ProjectionRows:
    """Derive typed release rows without database access or canonical writes."""
    unsupported_versions = set(tagged.schema_versions) - (
        SUPPORTED_CANONICAL_SCHEMA_VERSIONS
    )
    if unsupported_versions:
        raise PipelineError(
            "unsupported_canonical_schema",
            "unsupported canonical schema versions: "
            + ", ".join(sorted(unsupported_versions)),
        )
    grouped = {
        act["act_id"]: provisions
        for act, provisions in zip(
            tagged.records.acts, tagged.records.provision_groups, strict=True
        )
    }
    acts = tuple(
        _act_row(act)
        for act in sorted(tagged.records.acts, key=lambda record: record["act_id"])
    )
    provisions = tuple(
        row
        for act in sorted(tagged.records.acts, key=lambda record: record["act_id"])
        for row in _provision_rows(act, grouped[act["act_id"]])
    )
    return ProjectionRows(
        release_tag=tagged.release_tag,
        commit_sha=tagged.commit_sha,
        canonical_schema_versions=tagged.schema_versions,
        projection_schema_version=PROJECTION_SCHEMA_VERSION,
        sources=_source_rows(tagged.records),
        acts=acts,
        provisions=provisions,
        citations=_citation_rows(tagged.records),
    )


if __name__ == "__main__":
    raise SystemExit(main())
