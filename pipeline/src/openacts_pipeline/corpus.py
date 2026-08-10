"""Materialize reviewable corpus candidates and promote reviewed records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from openacts_pipeline.common import PipelineError, verify_cached_pdf

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schemas"
DEFAULT_CACHE_ROOT = REPO_ROOT / "source-cache"
DEFAULT_CORPUS_ROOT = REPO_ROOT / "corpus"
SCHEMA_FILES = {
    "act": "act.schema.json",
    "provision": "provision.schema.json",
    "source": "source.schema.json",
    "citation": "citation.schema.json",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _act_id_from_provision_id(provision_id: str) -> str:
    return provision_id.split(":", 1)[0]


def validate_table(table: dict[str, Any]) -> None:
    """Validate logical table relationships that JSON Schema cannot express."""
    column_count = table["column_count"]
    rows: dict[str, str] = {}
    cells: dict[str, dict[str, Any]] = {}

    for group in table["row_groups"]:
        occupancy: dict[tuple[int, int], str] = {}
        group_rows = group["rows"]
        for row_index, row in enumerate(group_rows):
            row_id = row["row_id"]
            _require(row_id not in rows, f"duplicate table row ID: {row_id}")
            rows[row_id] = group["role"]

            for cell in row["cells"]:
                cell_id = cell["cell_id"]
                _require(cell_id not in cells, f"duplicate table cell ID: {cell_id}")
                cells[cell_id] = cell
                last_column = cell["column_start"] + cell["column_span"] - 1
                last_row = row_index + cell["row_span"] - 1
                _require(
                    last_column <= column_count,
                    f"table cell {cell_id} exceeds column_count",
                )
                _require(
                    last_row < len(group_rows),
                    f"table cell {cell_id} exceeds its row group",
                )
                for covered_row in range(row_index, last_row + 1):
                    for covered_column in range(cell["column_start"], last_column + 1):
                        position = (covered_row, covered_column)
                        _require(
                            position not in occupancy,
                            f"table cell {cell_id} overlaps {occupancy.get(position)}",
                        )
                        occupancy[position] = cell_id

    for cell in cells.values():
        for header_id in cell["header_cell_ids"]:
            _require(header_id in cells, f"unknown table header cell: {header_id}")
            _require(
                cells[header_id]["role"] == "header",
                f"table header reference is not a header: {header_id}",
            )

    for segment in table["source_segments"]:
        for row_id in segment["row_ids"]:
            _require(row_id in rows, f"unknown table segment row: {row_id}")
        for row_id in segment["repeated_header_row_ids"]:
            _require(row_id in rows, f"unknown repeated header row: {row_id}")
            _require(
                rows[row_id] == "header", f"repeated row is not a header: {row_id}"
            )
            _require(
                row_id in segment["row_ids"],
                f"repeated header is absent from segment rows: {row_id}",
            )


def _collect_provision_content(
    provision: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    blocks_by_id: dict[str, dict[str, Any]] = {}
    content_ids: set[str] = set()
    source_spans = list(provision["source_spans"])

    def register(content_id: str) -> None:
        _require(
            content_id not in content_ids,
            f"duplicate content ID in {provision['provision_id']}: {content_id}",
        )
        content_ids.add(content_id)

    def collect_blocks(blocks: list[dict[str, Any]]) -> None:
        for block in blocks:
            block_id = block["block_id"]
            register(block_id)
            blocks_by_id[block_id] = block
            source_spans.extend(block["source_spans"])

            if block["kind"] == "list":
                for item in block["items"]:
                    register(item["item_id"])
                    source_spans.extend(item["source_spans"])
                    collect_blocks(item["content_blocks"])
            elif block["kind"] == "table":
                validate_table(block)
                if block["caption"] is not None:
                    source_spans.extend(block["caption"]["source_spans"])
                for group in block["row_groups"]:
                    register(group["group_id"])
                    for row in group["rows"]:
                        register(row["row_id"])
                        for cell in row["cells"]:
                            register(cell["cell_id"])
                            source_spans.extend(cell["source_spans"])
                            collect_blocks(cell["content_blocks"])
                collect_blocks(block["notes"])
                for segment in block["source_segments"]:
                    register(segment["segment_id"])
                    source_spans.extend(segment["source_spans"])

    collect_blocks(provision["content_blocks"])
    return blocks_by_id, source_spans


def validate_corpus(
    acts: list[dict[str, Any]],
    provisions: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> None:
    """Validate relationships across individually schema-valid records."""
    record_groups = {
        "act": (acts, "act_id"),
        "provision": (provisions, "provision_id"),
        "source": (sources, "source_id"),
        "citation": (citations, "citation_id"),
    }
    seen_ids: dict[str, str] = {}
    for record_type, (records, id_field) in record_groups.items():
        for record in records:
            record_id = record[id_field]
            _require(record_id not in seen_ids, f"duplicate record ID: {record_id}")
            seen_ids[record_id] = record_type

    acts_by_id = {act["act_id"]: act for act in acts}
    provisions_by_id = {
        provision["provision_id"]: provision for provision in provisions
    }
    sources_by_id = {source["source_id"]: source for source in sources}

    act_source_ids: dict[str, set[str]] = {}
    for act in acts:
        act_id = act["act_id"]
        linked_source_ids = {relation["source_id"] for relation in act["source_refs"]}
        act_source_ids[act_id] = linked_source_ids
        evidence_source_ids = set(act["status_source_ids"])
        for date_claim in act["dates"].values():
            evidence_source_ids.update(date_claim["source_ids"])
        for note in act["editorial_notes"]:
            evidence_source_ids.update(note["source_ids"])

        for source_id in linked_source_ids | evidence_source_ids:
            _require(source_id in sources_by_id, f"unknown Act Source: {source_id}")
        _require(
            evidence_source_ids <= linked_source_ids,
            f"Act evidence is absent from source_refs: {act_id}",
        )

    children: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    content_by_provision: dict[str, dict[str, dict[str, Any]]] = {}
    for provision in provisions:
        provision_id = provision["provision_id"]
        act_id = _act_id_from_provision_id(provision_id)
        _require(act_id in acts_by_id, f"unknown owning Act: {act_id}")

        parent_id = provision["parent_provision_id"]
        if parent_id is not None:
            _require(
                parent_id in provisions_by_id,
                f"unknown Provision parent: {parent_id}",
            )
            _require(
                _act_id_from_provision_id(parent_id) == act_id,
                f"cross-Act Provision parent: {parent_id}",
            )
        children[(act_id, parent_id)].append(provision["order"])

        blocks, source_spans = _collect_provision_content(provision)
        content_by_provision[provision_id] = blocks
        for span in source_spans:
            source_id = span["source_id"]
            _require(
                source_id in sources_by_id, f"unknown Provision Source: {source_id}"
            )
            _require(
                source_id in act_source_ids[act_id],
                f"Provision Source is not linked by its Act: {source_id}",
            )
            _require(
                span["pdf_page"] <= sources_by_id[source_id]["page_count"],
                f"PDF page exceeds Source page_count: {source_id}",
            )

    for (act_id, parent_id), orders in children.items():
        _require(
            sorted(orders) == list(range(1, len(orders) + 1)),
            f"incomplete sibling order under {parent_id or act_id}",
        )

    for provision in provisions:
        seen_parents: set[str] = set()
        parent_id = provision["parent_provision_id"]
        while parent_id is not None:
            _require(
                parent_id not in seen_parents, f"Provision parent cycle at {parent_id}"
            )
            seen_parents.add(parent_id)
            parent_id = provisions_by_id[parent_id]["parent_provision_id"]

    for citation in citations:
        source_provision_id = citation["source_provision_id"]
        _require(
            source_provision_id in provisions_by_id,
            f"unknown Citation source Provision: {source_provision_id}",
        )
        source_act_id = _act_id_from_provision_id(source_provision_id)
        _require(
            citation["citation_id"].startswith(f"citation:{source_act_id}:"),
            f"Citation ID belongs to the wrong Act: {citation['citation_id']}",
        )

        block_id = citation["source_block_id"]
        blocks = content_by_provision[source_provision_id]
        _require(block_id in blocks, f"unknown Citation source block: {block_id}")
        block = blocks[block_id]
        _require("text" in block, f"Citation source block has no text: {block_id}")
        start = citation["text_range"]["start"]
        end = citation["text_range"]["end"]
        _require(
            start < end <= len(block["text"]),
            f"invalid Citation text range: {start}:{end}",
        )

        target_act_id = citation["target"]["act_id"]
        _require(
            target_act_id in acts_by_id, f"unknown Citation target Act: {target_act_id}"
        )
        target_provision_id = citation["target"]["provision_id"]
        if target_provision_id is not None:
            _require(
                target_provision_id in provisions_by_id,
                f"unknown Citation target Provision: {target_provision_id}",
            )
            _require(
                _act_id_from_provision_id(target_provision_id) == target_act_id,
                f"Citation target Act and Provision disagree: {citation['citation_id']}",
            )


def _registry() -> Registry:
    return Registry().with_resources(
        (
            path.resolve().as_uri(),
            Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))),
        )
        for path in SCHEMA_DIR.glob("*.schema.json")
    )


def _validate_record(record_type: str, record: dict[str, Any]) -> None:
    schema_path = SCHEMA_DIR / SCHEMA_FILES[record_type]
    validator = Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": schema_path.resolve().as_uri(),
        },
        registry=_registry(),
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(record), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.path) or record_type
        raise PipelineError("invalid_corpus_record", f"{field}: {error.message}")


def _read_json(path: Path, *, code: str = "invalid_input") -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(code, f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(code, f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PipelineError("invalid_candidate", f"cannot read {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "invalid_candidate", f"{path}:{line_number}: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise PipelineError(
                "invalid_candidate", f"{path}:{line_number} must be a JSON object"
            )
        records.append(record)
    return records


def _load_stage(path: Path, stage: str) -> dict[str, Any]:
    value = _read_json(path)
    if value.get("stage") != stage or value.get("status") != "success":
        raise PipelineError(
            "invalid_input", f"{path} is not a successful {stage} artifact"
        )
    return value


def _cache_reference(cache_root: Path, reference: object) -> Path:
    if not isinstance(reference, str) or not reference:
        raise PipelineError("invalid_input", "pipeline artifact reference is missing")
    path = (cache_root / reference).resolve()
    if not path.is_relative_to(cache_root.resolve()):
        raise PipelineError(
            "invalid_input", "pipeline artifact reference leaves the cache"
        )
    return path


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")


def _roman_to_int(value: str) -> int | None:
    roman = value.upper()
    if not roman or re.fullmatch(r"[IVXLCDM]+", roman) is None:
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for character in reversed(roman):
        current = values[character]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total


def _marker(node: dict[str, Any]) -> str | None:
    label = node.get("display_label")
    if not isinstance(label, str):
        return None
    value = label.strip()
    value = re.sub(
        r"^(?:PART|CHAPTER|DIVISION|SECTION)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = value.strip(" \t\r\n().:-")
    if node["node_type"] in {"part", "chapter", "division", "schedule_part"}:
        roman = _roman_to_int(value)
        if roman is not None:
            return str(roman)
    return _slug(value) or None


def _definition_term(node: dict[str, Any]) -> str | None:
    for block in node.get("content_blocks", []):
        text = block.get("text")
        if not isinstance(text, str):
            continue
        match = re.match(r'\s*["“‘\']([^"”’\']+)["”’\']', text)
        if match:
            return _slug(match.group(1)) or None
    return None


def _local_provision_id(
    node: dict[str, Any],
    parent_local_id: str | None,
    *,
    schedule_ordinal: int | None = None,
) -> str:
    node_type = node["node_type"]
    if node_type == "schedule":
        if schedule_ordinal is None:
            raise PipelineError("invalid_structure", "Schedule ordinal is missing")
        return f"schedule-{schedule_ordinal}"
    fixed = {
        "document_title": "document-title",
        "long_title": "long-title",
        "arrangement": "arrangement",
        "preamble": "preamble",
        "enacting_formula": "enacting-formula",
        "authentication": "authentication",
        "explanatory_note": "explanatory-note",
    }
    if node_type in fixed:
        return fixed[node_type]

    token = _definition_term(node) if node_type == "definition" else _marker(node)
    if token is None and node_type in {"cross_heading", "table", "form"}:
        heading = node.get("heading")
        if isinstance(heading, str):
            token = _slug(heading) or None
    if (
        token is None
        and parent_local_id is not None
        and node_type
        in {
            "subsection",
            "paragraph",
            "subparagraph",
            "schedule_paragraph",
            "schedule_subparagraph",
        }
    ):
        order = node.get("order")
        # Printed continuations have no marker; initial sibling order gives them a
        # readable ID without hashing text that may receive transcription fixes.
        if isinstance(order, int) and not isinstance(order, bool) and order >= 1:
            token = f"unnumbered-{order}"
    if token is None:
        raise PipelineError(
            "unassignable_provision_id",
            f"{node.get('draft_id', node_type)} has no stable label or definition term",
        )

    segment_type = {
        "schedule_part": "part",
        "schedule_paragraph": "paragraph",
        "schedule_subparagraph": "subparagraph",
    }.get(node_type, node_type.replace("_", "-"))
    segment = f"{segment_type}-{token}"
    if node_type in {"part", "chapter", "division", "section"}:
        return segment
    if parent_local_id is None:
        return segment
    parent_base = re.sub(r"~[0-9]+$", "", parent_local_id)
    return f"{parent_base}.{segment}"


def _materialize_provisions(
    act_id: str, drafts: list[dict[str, Any]], source_id: str
) -> list[dict[str, Any]]:
    draft_to_local: dict[str, str] = {}
    used: defaultdict[str, int] = defaultdict(int)
    provisions: list[dict[str, Any]] = []
    schedule_ordinal = 0

    for draft in drafts:
        draft_id = draft.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            raise PipelineError("invalid_structure", "Provision draft_id is missing")
        if draft_id in draft_to_local:
            raise PipelineError("invalid_structure", f"duplicate draft ID: {draft_id}")
        parent_draft_id = draft.get("parent_draft_id")
        if parent_draft_id is not None and parent_draft_id not in draft_to_local:
            raise PipelineError(
                "invalid_structure", f"parent does not precede child: {parent_draft_id}"
            )
        parent_local_id = (
            draft_to_local[parent_draft_id] if parent_draft_id is not None else None
        )
        if draft["node_type"] == "schedule":
            schedule_ordinal += 1
        base_local_id = _local_provision_id(
            draft,
            parent_local_id,
            schedule_ordinal=(
                schedule_ordinal if draft["node_type"] == "schedule" else None
            ),
        )
        used[base_local_id] += 1
        collision = used[base_local_id]
        local_id = base_local_id if collision == 1 else f"{base_local_id}~{collision}"
        draft_to_local[draft_id] = local_id

        spans = draft.get("source_spans", [])
        nested_sources = {
            span.get("source_id") for span in spans if isinstance(span, dict)
        }
        if nested_sources != {source_id}:
            raise PipelineError(
                "source_mismatch", f"{draft_id} does not point to the structured Source"
            )
        provisions.append(
            {
                "schema_version": "0.1.0",
                "record_type": "provision",
                "provision_id": f"{act_id}:{local_id}",
                "node_type": draft["node_type"],
                "display_label": draft["display_label"],
                "heading": draft["heading"],
                "parent_provision_id": (
                    f"{act_id}:{parent_local_id}"
                    if parent_local_id is not None
                    else None
                ),
                "order": draft["order"],
                "source_spans": spans,
                "content_blocks": draft["content_blocks"],
                "text_fidelity": "machine_extracted",
            }
        )
    return provisions


def _json_bytes(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    ).encode()


def _act_relative_dir(act: dict[str, Any]) -> Path:
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


def _candidate_files(
    act: dict[str, Any], provisions: list[dict[str, Any]], source: dict[str, Any]
) -> dict[Path, bytes]:
    act_dir = _act_relative_dir(act)
    return {
        Path("sources.jsonl"): _jsonl_bytes([source]),
        act_dir / "act.json": _json_bytes(act),
        act_dir / "provisions.jsonl": _jsonl_bytes(provisions),
        act_dir / "citations.jsonl": b"",
    }


def _write_candidate(destination: Path, files: dict[Path, bytes]) -> bool:
    if destination.exists():
        for relative_path, expected in files.items():
            path = destination / relative_path
            try:
                actual = path.read_bytes()
            except OSError as exc:
                raise PipelineError(
                    "candidate_exists", f"cannot verify existing candidate: {exc}"
                ) from exc
            if actual != expected:
                raise PipelineError(
                    "candidate_exists",
                    "candidate already exists with edits; refusing to overwrite it",
                )
        return True

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        for relative_path, content in files.items():
            path = temporary / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return False


def _validate_records(
    act: dict[str, Any],
    provisions: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> None:
    _validate_record("act", act)
    for provision in provisions:
        _validate_record("provision", provision)
    for source in sources:
        _validate_record("source", source)
    for citation in citations:
        _validate_record("citation", citation)
    try:
        validate_corpus([act], provisions, sources, citations)
    except (AssertionError, KeyError, TypeError) as exc:
        raise PipelineError("invalid_corpus", str(exc)) from exc


def candidate(
    structure_path: Path,
    act_path: Path,
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
) -> dict[str, Any]:
    """Create an exact corpus-shaped candidate without changing the corpus."""
    structure_artifact = _load_stage(structure_path, "structure")
    act = _read_json(act_path, code="invalid_act")
    _validate_record("act", act)

    extraction = _load_stage(
        _cache_reference(cache_root, structure_artifact.get("input_extraction")),
        "extract",
    )
    classification = _load_stage(
        _cache_reference(cache_root, extraction.get("input_classification")),
        "classify",
    )
    receipt = _read_json(
        _cache_reference(cache_root, classification.get("input_receipt"))
    )
    if receipt.get("status") != "success" or not isinstance(
        receipt.get("source"), dict
    ):
        raise PipelineError("invalid_input", "acquisition receipt is not successful")

    source_id = structure_artifact.get("source_id")
    chain_ids = {
        extraction.get("source_id"),
        classification.get("source_id"),
        receipt["source"].get("source_id"),
    }
    if not isinstance(source_id, str) or chain_ids != {source_id}:
        raise PipelineError(
            "source_mismatch", "pipeline artifacts identify different Sources"
        )

    authoritative = [
        relation
        for relation in act["source_refs"]
        if relation["role"] == "authoritative_text"
    ]
    if authoritative[0]["source_id"] != source_id:
        raise PipelineError(
            "source_mismatch",
            "Act authoritative Source differs from the structure artifact",
        )

    source = dict(receipt["source"])
    source["text_layer"] = classification.get("summary", {}).get("proposed_text_layer")
    _validate_record("source", source)
    digest = source_id.removeprefix("sha256:")
    cache_path = receipt.get("cache_path")
    if not isinstance(cache_path, str):
        raise PipelineError("invalid_input", "acquisition receipt has no cache_path")
    verify_cached_pdf(
        cache_root,
        Path(cache_path),
        expected_byte_length=source["byte_length"],
        expected_digest=digest,
    )

    drafts = structure_artifact.get("provisions")
    if not isinstance(drafts, list) or not all(
        isinstance(item, dict) for item in drafts
    ):
        raise PipelineError("invalid_structure", "structure provisions must be objects")
    provisions = _materialize_provisions(act["act_id"], drafts, source_id)
    _validate_records(act, provisions, [source], [])
    files = _candidate_files(act, provisions, source)
    candidate_digest = hashlib.sha256()
    for path, content in sorted(files.items(), key=lambda item: item[0].as_posix()):
        candidate_digest.update(path.as_posix().encode())
        candidate_digest.update(b"\0")
        candidate_digest.update(content)
    destination = (
        cache_root
        / "corpus-candidates"
        / f"{act['act_id']}-{candidate_digest.hexdigest()[:12]}"
    )
    reused = _write_candidate(destination, files)
    return {
        "status": "success",
        "network_access": False,
        "candidate_path": destination.relative_to(cache_root).as_posix(),
        "act_id": act["act_id"],
        "source_id": source_id,
        "provision_count": len(provisions),
        "citation_count": 0,
        "review_required": True,
        "reused": reused,
    }


def _load_candidate(
    candidate_path: Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    Path,
]:
    try:
        act_paths = list(candidate_path.glob("**/act.json"))
    except OSError as exc:
        raise PipelineError(
            "invalid_candidate", f"cannot inspect candidate: {exc}"
        ) from exc
    if len(act_paths) != 1:
        raise PipelineError(
            "invalid_candidate", "candidate must contain exactly one act.json"
        )
    act_path = act_paths[0]
    act_dir = act_path.parent
    act = _read_json(act_path, code="invalid_candidate")
    sources = _read_jsonl(candidate_path / "sources.jsonl")
    provisions = _read_jsonl(act_dir / "provisions.jsonl")
    citations = _read_jsonl(act_dir / "citations.jsonl")
    if len(sources) != 1:
        raise PipelineError(
            "invalid_candidate", "candidate must contain exactly one Source"
        )
    _validate_records(act, provisions, sources, citations)
    if act_dir.relative_to(candidate_path) != _act_relative_dir(act):
        raise PipelineError(
            "invalid_candidate", "candidate Act directory does not match act_id"
        )
    return act, provisions, sources, citations, act_dir


def _verify_candidate_source(cache_root: Path, source: dict[str, Any]) -> None:
    digest = source["source_id"].removeprefix("sha256:")
    verify_cached_pdf(
        cache_root,
        Path("sha256", digest[:2], f"{digest}.pdf"),
        expected_byte_length=source["byte_length"],
        expected_digest=digest,
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def promote(
    candidate_path: Path,
    *,
    execute: bool = False,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> dict[str, Any]:
    """Validate a candidate and explicitly promote it into the authored corpus."""
    act, provisions, sources, citations, _candidate_act_dir = _load_candidate(
        candidate_path
    )
    source = sources[0]
    _verify_candidate_source(cache_root, source)
    machine_count = sum(
        provision["text_fidelity"] == "machine_extracted" for provision in provisions
    )
    target_relative = _act_relative_dir(act)
    target_dir = corpus_root / target_relative
    existing_sources = (
        _read_jsonl(corpus_root / "sources.jsonl")
        if (corpus_root / "sources.jsonl").exists()
        else []
    )
    by_id = {record["source_id"]: record for record in existing_sources}
    existing_source = by_id.get(source["source_id"])
    source_conflict = existing_source is not None and existing_source != source
    ready = machine_count == 0 and not target_dir.exists() and not source_conflict
    blockers: list[str] = []
    if machine_count:
        blockers.append(f"{machine_count} Provisions remain machine_extracted")
    if target_dir.exists():
        blockers.append("target Act directory already exists")
    if source_conflict:
        blockers.append("corpus Source with this ID has different metadata")

    result = {
        "status": "ready" if ready else "review_required",
        "network_access": False,
        "execute": execute,
        "ready": ready,
        "act_id": act["act_id"],
        "source_id": source["source_id"],
        "provision_count": len(provisions),
        "citation_count": len(citations),
        "target_path": target_dir.as_posix(),
        "blockers": blockers,
    }
    if not execute:
        return result
    if not ready:
        raise PipelineError("promotion_blocked", "; ".join(blockers))

    if existing_source is None:
        by_id[source["source_id"]] = source
        _atomic_write(
            corpus_root / "sources.jsonl",
            _jsonl_bytes(
                sorted(by_id.values(), key=lambda record: record["source_id"])
            ),
        )

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target_dir.name}-", dir=target_dir.parent)
    )
    try:
        (temporary / "act.json").write_bytes(_json_bytes(act))
        (temporary / "provisions.jsonl").write_bytes(_jsonl_bytes(provisions))
        (temporary / "citations.jsonl").write_bytes(_jsonl_bytes(citations))
        os.replace(temporary, target_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    result["status"] = "success"
    return result
