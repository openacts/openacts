"""Build a deterministic PostgreSQL projection from an exact corpus tag."""

from __future__ import annotations

import re
import subprocess
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from openacts_pipeline.common import PipelineError
from openacts_pipeline.corpus import load_corpus
from openacts_pipeline.corpus_files import CorpusRecords

RELEASE_TAG_PATTERN = re.compile(r"corpus-v[0-9]+\.[0-9]+\.[0-9]+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
PROJECTION_SCHEMA_VERSION = 1
SUPPORTED_CANONICAL_SCHEMA_VERSIONS = {"0.1.0"}
TEXT_BLOCK_KINDS = {"text", "quoted_text", "formula", "signature"}


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
        raise PipelineError(
            "invalid_release_tag", "release must match corpus-vX.Y.Z"
        )
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
        records = load_corpus(
            temporary / "corpus", schema_dir=temporary / "schemas"
        )
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
    title_values = [titles["official"], titles["short"], titles["long"], *act["aliases"]]
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
