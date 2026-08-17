"""Read and serialize the canonical corpus file layout."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openacts_pipeline.common import PipelineError


@dataclass(frozen=True, slots=True)
class CorpusRecords:
    """Canonical records grouped by their authored Act directories."""

    sources: tuple[dict[str, Any], ...]
    act_directories: tuple[Path, ...]
    acts: tuple[dict[str, Any], ...]
    provision_groups: tuple[tuple[dict[str, Any], ...], ...]
    citation_groups: tuple[tuple[dict[str, Any], ...], ...]

    @property
    def provisions(self) -> tuple[dict[str, Any], ...]:
        return tuple(record for group in self.provision_groups for record in group)

    @property
    def citations(self) -> tuple[dict[str, Any], ...]:
        return tuple(record for group in self.citation_groups for record in group)

    @property
    def schema_versions(self) -> tuple[str, ...]:
        records = (*self.sources, *self.acts, *self.provisions, *self.citations)
        return tuple(sorted({record["schema_version"] for record in records}))


def read_json(path: Path, *, code: str = "invalid_input") -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(code, f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(code, f"{path} must contain a JSON object")
    return value


def read_jsonl(
    path: Path, *, code: str = "invalid_candidate"
) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PipelineError(code, f"cannot read {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PipelineError(code, f"{path}:{line_number}: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise PipelineError(
                code, f"{path}:{line_number} must be a JSON object"
            )
        records.append(record)
    return records


def json_bytes(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()


def jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    ).encode()


def act_relative_dir(act: dict[str, Any]) -> Path:
    prefix = f"{act['jurisdiction']}-act-{act['year']}-"
    act_id = act["act_id"]
    if not act_id.startswith(prefix):
        raise PipelineError("invalid_act_path", f"act_id must begin with {prefix}")
    slug = act_id.removeprefix(prefix)
    country = act["country_code"].lower()
    jurisdiction_parts = act["jurisdiction"].split("-")
    if jurisdiction_parts[0] != country:
        raise PipelineError(
            "invalid_act_path", "country_code and jurisdiction disagree"
        )
    return Path(country, *jurisdiction_parts[1:], "acts", str(act["year"]), slug)


def read_corpus_records(corpus_root: Path) -> CorpusRecords:
    """Read the exact canonical layout without validating record contents."""
    if not corpus_root.is_dir():
        raise PipelineError(
            "invalid_corpus", f"corpus directory does not exist: {corpus_root}"
        )

    try:
        act_paths = sorted(corpus_root.glob("**/act.json"))
    except OSError as exc:
        raise PipelineError("invalid_corpus", f"cannot inspect corpus: {exc}") from exc
    if not act_paths:
        raise PipelineError("invalid_corpus", "corpus contains no Acts")

    expected_files = {corpus_root / "sources.jsonl"}
    for act_path in act_paths:
        expected_files.update(
            {
                act_path,
                act_path.parent / "provisions.jsonl",
                act_path.parent / "citations.jsonl",
            }
        )
    try:
        actual_files = {path for path in corpus_root.rglob("*") if path.is_file()}
    except OSError as exc:
        raise PipelineError("invalid_corpus", f"cannot inspect corpus: {exc}") from exc
    if actual_files != expected_files:
        missing = sorted(path.relative_to(corpus_root) for path in expected_files - actual_files)
        unexpected = sorted(path.relative_to(corpus_root) for path in actual_files - expected_files)
        details = []
        if missing:
            details.append("missing: " + ", ".join(map(str, missing)))
        if unexpected:
            details.append("unexpected: " + ", ".join(map(str, unexpected)))
        raise PipelineError("invalid_corpus", "; ".join(details))

    acts = tuple(read_json(path, code="invalid_corpus") for path in act_paths)
    provision_groups = tuple(
        tuple(read_jsonl(path.parent / "provisions.jsonl", code="invalid_corpus"))
        for path in act_paths
    )
    citation_groups = tuple(
        tuple(read_jsonl(path.parent / "citations.jsonl", code="invalid_corpus"))
        for path in act_paths
    )
    return CorpusRecords(
        sources=tuple(
            read_jsonl(corpus_root / "sources.jsonl", code="invalid_corpus")
        ),
        act_directories=tuple(path.parent.relative_to(corpus_root) for path in act_paths),
        acts=acts,
        provision_groups=provision_groups,
        citation_groups=citation_groups,
    )
