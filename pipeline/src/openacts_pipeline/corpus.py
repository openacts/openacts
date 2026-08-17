"""Materialize reviewable corpus candidates and promote reviewed records."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from openacts_pipeline.common import PipelineError, verify_cached_pdf
from openacts_pipeline.corpus_files import (
    CorpusRecords,
    act_relative_dir,
    json_bytes,
    jsonl_bytes,
    read_corpus_records,
    read_json,
    read_jsonl,
)
from openacts_pipeline.corpus_schema import build_registry, validate_record

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schemas"
DEFAULT_CACHE_ROOT = REPO_ROOT / "source-cache"
DEFAULT_CORPUS_ROOT = REPO_ROOT / "corpus"
CANDIDATE_VERSION = 1


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

    children: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
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
        children[(act_id, parent_id)].append(provision)

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

    for (act_id, parent_id), siblings in children.items():
        orders = [provision["order"] for provision in siblings]
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

    for act_id in acts_by_id:
        expected_ids: list[str] = []
        stack = sorted(
            children[(act_id, None)], key=lambda provision: provision["order"], reverse=True
        )
        while stack:
            provision = stack.pop()
            provision_id = provision["provision_id"]
            expected_ids.append(provision_id)
            stack.extend(
                sorted(
                    children[(act_id, provision_id)],
                    key=lambda child: child["order"],
                    reverse=True,
                )
            )
        actual_ids = [
            provision["provision_id"]
            for provision in provisions
            if _act_id_from_provision_id(provision["provision_id"]) == act_id
        ]
        _require(
            actual_ids == expected_ids,
            f"Provisions are not stored in legal order: {act_id}",
        )

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


def _load_stage(path: Path, stage: str) -> dict[str, Any]:
    value = read_json(path)
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


def _ordered_provision_ids_sha256(provisions: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for provision in provisions:
        digest.update(provision["provision_id"].encode())
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def load_corpus(
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    *,
    schema_dir: Path = SCHEMA_DIR,
) -> CorpusRecords:
    """Load and validate one complete canonical corpus directory."""
    records = read_corpus_records(corpus_root)
    registry = build_registry(schema_dir)
    for source in records.sources:
        validate_record("source", source, schema_dir=schema_dir, registry=registry)
    source_ids = [source["source_id"] for source in records.sources]
    if source_ids != sorted(source_ids):
        raise PipelineError(
            "invalid_corpus", "sources.jsonl must be sorted by source_id"
        )
    for act, relative_dir, provisions, citations in zip(
        records.acts,
        records.act_directories,
        records.provision_groups,
        records.citation_groups,
        strict=True,
    ):
        validate_record("act", act, schema_dir=schema_dir, registry=registry)
        if relative_dir != act_relative_dir(act):
            raise PipelineError(
                "invalid_corpus", f"Act directory does not match {act['act_id']}"
            )
        for provision in provisions:
            validate_record(
                "provision", provision, schema_dir=schema_dir, registry=registry
            )
            if not provision["provision_id"].startswith(f"{act['act_id']}:"):
                raise PipelineError(
                    "invalid_corpus",
                    f"Provision is stored under the wrong Act: "
                    f"{provision['provision_id']}",
                )
        for citation in citations:
            validate_record(
                "citation", citation, schema_dir=schema_dir, registry=registry
            )
            if not citation["source_provision_id"].startswith(f"{act['act_id']}:"):
                raise PipelineError(
                    "invalid_corpus",
                    f"Citation is stored under the wrong Act: {citation['citation_id']}",
                )
    try:
        validate_corpus(
            list(records.acts),
            list(records.provisions),
            list(records.sources),
            list(records.citations),
        )
    except (AssertionError, KeyError, TypeError) as exc:
        raise PipelineError("invalid_corpus", str(exc)) from exc
    return records


def _candidate_files(
    act: dict[str, Any],
    provisions: list[dict[str, Any]],
    source: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[Path, bytes]:
    act_dir = act_relative_dir(act)
    return {
        Path("candidate.json"): json_bytes(manifest),
        Path("sources.jsonl"): jsonl_bytes([source]),
        act_dir / "act.json": json_bytes(act),
        act_dir / "provisions.jsonl": jsonl_bytes(provisions),
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
    registry = build_registry(SCHEMA_DIR)
    validate_record("act", act, registry=registry)
    for provision in provisions:
        validate_record("provision", provision, registry=registry)
    for source in sources:
        validate_record("source", source, registry=registry)
    for citation in citations:
        validate_record("citation", citation, registry=registry)
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
    act = read_json(act_path, code="invalid_act")
    validate_record("act", act)

    extraction = _load_stage(
        _cache_reference(cache_root, structure_artifact.get("input_extraction")),
        "extract",
    )
    classification = _load_stage(
        _cache_reference(cache_root, extraction.get("input_classification")),
        "classify",
    )
    receipt = read_json(
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
    validate_record("source", source)
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
    try:
        input_structure = structure_path.resolve().relative_to(cache_root.resolve())
    except ValueError as exc:
        raise PipelineError(
            "invalid_input", "structure artifact must be inside the source cache"
        ) from exc
    manifest = {
        "candidate_version": CANDIDATE_VERSION,
        "act_id": act["act_id"],
        "source_id": source_id,
        "input_structure": input_structure.as_posix(),
        "provision_count": len(provisions),
        "ordered_provision_ids_sha256": _ordered_provision_ids_sha256(provisions),
    }
    files = _candidate_files(act, provisions, source, manifest)
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
    manifest_path = candidate_path / "candidate.json"
    if not manifest_path.is_file():
        raise PipelineError(
            "invalid_candidate", "candidate manifest is missing; regenerate candidate"
        )
    manifest = read_json(manifest_path, code="invalid_candidate")
    expected_manifest_fields = {
        "candidate_version",
        "act_id",
        "source_id",
        "input_structure",
        "provision_count",
        "ordered_provision_ids_sha256",
    }
    if (
        set(manifest) != expected_manifest_fields
        or manifest.get("candidate_version") != CANDIDATE_VERSION
        or not isinstance(manifest.get("act_id"), str)
        or not isinstance(manifest.get("source_id"), str)
        or not isinstance(manifest.get("input_structure"), str)
        or not manifest.get("input_structure")
        or not isinstance(manifest.get("provision_count"), int)
        or isinstance(manifest.get("provision_count"), bool)
        or manifest.get("provision_count", 0) < 1
        or not isinstance(manifest.get("ordered_provision_ids_sha256"), str)
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            manifest.get("ordered_provision_ids_sha256", ""),
        )
        is None
    ):
        raise PipelineError("invalid_candidate", "candidate manifest is malformed")
    input_structure = Path(manifest["input_structure"])
    if input_structure.is_absolute() or ".." in input_structure.parts:
        raise PipelineError("invalid_candidate", "candidate manifest path is unsafe")

    act_path = act_paths[0]
    act_dir = act_path.parent
    act = read_json(act_path, code="invalid_candidate")
    sources = read_jsonl(candidate_path / "sources.jsonl")
    provisions = read_jsonl(act_dir / "provisions.jsonl")
    citations = read_jsonl(act_dir / "citations.jsonl")
    if len(sources) != 1:
        raise PipelineError(
            "invalid_candidate", "candidate must contain exactly one Source"
        )
    if manifest["act_id"] != act.get("act_id") or manifest["source_id"] != sources[
        0
    ].get("source_id"):
        raise PipelineError(
            "candidate_integrity_failed", "candidate Act or Source identity changed"
        )
    if manifest["provision_count"] != len(provisions):
        raise PipelineError(
            "candidate_integrity_failed",
            f"expected {manifest['provision_count']} provisions, found {len(provisions)}",
        )
    if manifest["ordered_provision_ids_sha256"] != _ordered_provision_ids_sha256(
        provisions
    ):
        raise PipelineError(
            "candidate_integrity_failed",
            "ordered Provision IDs differ from the generated candidate",
        )
    _validate_records(act, provisions, sources, citations)
    if act_dir.relative_to(candidate_path) != act_relative_dir(act):
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


def review(
    candidate_path: Path,
    fidelity: str,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Preview or atomically record a whole-candidate single review."""
    if fidelity != "single_reviewed":
        raise PipelineError(
            "invalid_review_fidelity", "review currently supports single_reviewed"
        )
    act, provisions, sources, _citations, candidate_act_dir = _load_candidate(
        candidate_path
    )
    before = dict(sorted(Counter(p["text_fidelity"] for p in provisions).items()))
    reviewed = [
        (
            {**provision, "text_fidelity": fidelity}
            if provision["text_fidelity"] == "machine_extracted"
            else provision
        )
        for provision in provisions
    ]
    changed = sum(
        before_provision["text_fidelity"] != after_provision["text_fidelity"]
        for before_provision, after_provision in zip(provisions, reviewed, strict=True)
    )
    after = dict(sorted(Counter(p["text_fidelity"] for p in reviewed).items()))
    if execute and changed:
        _atomic_write(candidate_act_dir / "provisions.jsonl", jsonl_bytes(reviewed))
        _load_candidate(candidate_path)

    return {
        "status": "success" if execute else "ready",
        "network_access": False,
        "execute": execute,
        "candidate_path": candidate_path.as_posix(),
        "act_id": act["act_id"],
        "source_id": sources[0]["source_id"],
        "target_fidelity": fidelity,
        "provision_count": len(provisions),
        "changed_provisions": changed,
        "before_fidelity": before,
        "after_fidelity": after,
        "reused": changed == 0,
    }


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
    target_relative = act_relative_dir(act)
    target_dir = corpus_root / target_relative
    existing_sources = (
        read_jsonl(corpus_root / "sources.jsonl")
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
            jsonl_bytes(
                sorted(by_id.values(), key=lambda record: record["source_id"])
            ),
        )

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target_dir.name}-", dir=target_dir.parent)
    )
    try:
        (temporary / "act.json").write_bytes(json_bytes(act))
        (temporary / "provisions.jsonl").write_bytes(jsonl_bytes(provisions))
        (temporary / "citations.jsonl").write_bytes(jsonl_bytes(citations))
        os.replace(temporary, target_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    result["status"] = "success"
    return result
