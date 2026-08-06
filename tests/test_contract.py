#!/usr/bin/env python3
"""Test the OpenActs Gate A data contract."""

import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, RefResolver

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "valid"
SCHEMA_FILES = {
    "act": "act.schema.json",
    "provision": "provision.schema.json",
    "source": "source.schema.json",
    "citation": "citation.schema.json",
    "table": "table.schema.json",
}


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def make_validator(record_type: str) -> Draft202012Validator:
    schema = load_json(SCHEMA_DIR / SCHEMA_FILES[record_type])
    return Draft202012Validator(
        schema,
        resolver=RefResolver(
            base_uri=f"{SCHEMA_DIR.resolve().as_uri()}/",
            referrer=schema,
        ),
        format_checker=FormatChecker(),
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def act_id_from_provision_id(provision_id: str) -> str:
    return provision_id.split(":", 1)[0]


def validate_table(table: dict[str, Any]) -> None:
    """Validate logical relationships that JSON Schema cannot express."""
    column_count = table["column_count"]
    rows: dict[str, str] = {}
    cells: dict[str, dict[str, Any]] = {}

    for group in table["row_groups"]:
        occupancy: dict[tuple[int, int], str] = {}
        group_rows = group["rows"]
        for row_index, row in enumerate(group_rows):
            row_id = row["row_id"]
            require(row_id not in rows, f"duplicate table row ID: {row_id}")
            rows[row_id] = group["role"]

            for cell in row["cells"]:
                cell_id = cell["cell_id"]
                require(cell_id not in cells, f"duplicate table cell ID: {cell_id}")
                cells[cell_id] = cell

                first_column = cell["column_start"]
                last_column = first_column + cell["column_span"] - 1
                last_row = row_index + cell["row_span"] - 1
                require(
                    last_column <= column_count,
                    f"table cell {cell_id} exceeds column_count",
                )
                require(
                    last_row < len(group_rows),
                    f"table cell {cell_id} exceeds its row group",
                )
                for covered_row in range(row_index, last_row + 1):
                    for covered_column in range(first_column, last_column + 1):
                        position = (covered_row, covered_column)
                        require(
                            position not in occupancy,
                            f"table cell {cell_id} overlaps {occupancy.get(position)}",
                        )
                        occupancy[position] = cell_id

    for cell in cells.values():
        for header_id in cell["header_cell_ids"]:
            require(header_id in cells, f"unknown table header cell: {header_id}")
            require(
                cells[header_id]["role"] == "header",
                f"table header reference is not a header: {header_id}",
            )

    for segment in table["source_segments"]:
        for row_id in segment["row_ids"]:
            require(row_id in rows, f"unknown table segment row: {row_id}")
        for row_id in segment["repeated_header_row_ids"]:
            require(row_id in rows, f"unknown repeated header row: {row_id}")
            require(rows[row_id] == "header", f"repeated row is not a header: {row_id}")
            require(
                row_id in segment["row_ids"],
                f"repeated header is absent from segment rows: {row_id}",
            )


def collect_provision_content(
    provision: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Collect addressable blocks and all nested Source spans."""
    blocks_by_id: dict[str, dict[str, Any]] = {}
    content_ids: set[str] = set()
    source_spans = list(provision["source_spans"])

    def register(content_id: str) -> None:
        require(
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
            require(record_id not in seen_ids, f"duplicate record ID: {record_id}")
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
            require(source_id in sources_by_id, f"unknown Act Source: {source_id}")
        require(
            evidence_source_ids <= linked_source_ids,
            f"Act evidence is absent from source_refs: {act_id}",
        )

    children: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    content_by_provision: dict[str, dict[str, dict[str, Any]]] = {}
    for provision in provisions:
        provision_id = provision["provision_id"]
        act_id = act_id_from_provision_id(provision_id)
        require(act_id in acts_by_id, f"unknown owning Act: {act_id}")

        parent_id = provision["parent_provision_id"]
        if parent_id is not None:
            require(
                parent_id in provisions_by_id, f"unknown Provision parent: {parent_id}"
            )
            require(
                act_id_from_provision_id(parent_id) == act_id,
                f"cross-Act Provision parent: {parent_id}",
            )
        children[(act_id, parent_id)].append(provision["order"])

        blocks, source_spans = collect_provision_content(provision)
        content_by_provision[provision_id] = blocks
        for span in source_spans:
            source_id = span["source_id"]
            require(
                source_id in sources_by_id, f"unknown Provision Source: {source_id}"
            )
            require(
                source_id in act_source_ids[act_id],
                f"Provision Source is not linked by its Act: {source_id}",
            )
            require(
                span["pdf_page"] <= sources_by_id[source_id]["page_count"],
                f"PDF page exceeds Source page_count: {source_id}",
            )

    for (act_id, parent_id), orders in children.items():
        require(
            sorted(orders) == list(range(1, len(orders) + 1)),
            f"incomplete sibling order under {parent_id or act_id}",
        )

    for provision in provisions:
        seen_parents: set[str] = set()
        parent_id = provision["parent_provision_id"]
        while parent_id is not None:
            require(
                parent_id not in seen_parents, f"Provision parent cycle at {parent_id}"
            )
            seen_parents.add(parent_id)
            parent_id = provisions_by_id[parent_id]["parent_provision_id"]

    for citation in citations:
        source_provision_id = citation["source_provision_id"]
        require(
            source_provision_id in provisions_by_id,
            f"unknown Citation source Provision: {source_provision_id}",
        )
        source_act_id = act_id_from_provision_id(source_provision_id)
        require(
            citation["citation_id"].startswith(f"citation:{source_act_id}:"),
            f"Citation ID belongs to the wrong Act: {citation['citation_id']}",
        )

        block_id = citation["source_block_id"]
        blocks = content_by_provision[source_provision_id]
        require(block_id in blocks, f"unknown Citation source block: {block_id}")
        block = blocks[block_id]
        require("text" in block, f"Citation source block has no text: {block_id}")
        start = citation["text_range"]["start"]
        end = citation["text_range"]["end"]
        require(
            start < end <= len(block["text"]),
            f"invalid Citation text range: {start}:{end}",
        )

        target_act_id = citation["target"]["act_id"]
        require(
            target_act_id in acts_by_id, f"unknown Citation target Act: {target_act_id}"
        )
        target_provision_id = citation["target"]["provision_id"]
        if target_provision_id is not None:
            require(
                target_provision_id in provisions_by_id,
                f"unknown Citation target Provision: {target_provision_id}",
            )
            require(
                act_id_from_provision_id(target_provision_id) == target_act_id,
                f"Citation target Act and Provision disagree: {citation['citation_id']}",
            )


def assert_invalid_corpus(name: str, **records: list[dict[str, Any]]) -> None:
    try:
        validate_corpus(**records)
    except AssertionError:
        return
    raise AssertionError(f"{name} should be rejected")


def test_contract() -> None:
    for schema_path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        Draft202012Validator.check_schema(load_json(schema_path))

    fixtures = {
        record_type: load_json(FIXTURE_DIR / f"{record_type}.json")
        for record_type in SCHEMA_FILES
    }
    validators = {
        record_type: make_validator(record_type) for record_type in SCHEMA_FILES
    }
    for record_type, fixture in fixtures.items():
        validators[record_type].validate(fixture)
    constitution = load_json(FIXTURE_DIR / "constitution.json")
    validators["act"].validate(constitution)

    legacy_provision = copy.deepcopy(fixtures["provision"])
    legacy_provision["version_id"] = "ng-federal-act-2023-37@2023-06-12:eng"

    unknown_node_type = copy.deepcopy(fixtures["provision"])
    unknown_node_type["node_type"] = "clauseish"

    legacy_proviso = copy.deepcopy(fixtures["provision"])
    legacy_proviso["node_type"] = "proviso"

    legacy_document_type = copy.deepcopy(fixtures["act"])
    legacy_document_type["document_type"] = "constitution"

    duplicated_language = copy.deepcopy(fixtures["act"])
    duplicated_language["language"] = "eng"

    unknown_act_status = copy.deepcopy(fixtures["act"])
    unknown_act_status["status"] = "active"

    duplicate_authority = copy.deepcopy(fixtures["act"])
    duplicate_authority["source_refs"].append(
        {
            "source_id": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "role": "authoritative_text",
            "scope_note": "Competing authoritative text.",
        }
    )

    owned_source = copy.deepcopy(fixtures["source"])
    owned_source["act_id"] = fixtures["act"]["act_id"]

    html_source = copy.deepcopy(fixtures["source"])
    html_source["media_type"] = "text/html"

    workflow_citation = copy.deepcopy(fixtures["citation"])
    workflow_citation["resolution_status"] = "resolved"

    invalid_table = copy.deepcopy(fixtures["table"])
    invalid_table["row_groups"][0]["rows"][0]["cells"][0]["blank"] = True

    null_printed_page = copy.deepcopy(fixtures["provision"])
    null_printed_page["content_blocks"][0]["source_spans"][0]["printed_page"] = None

    invalid_records = [
        ("Version-owned Provision", "provision", legacy_provision),
        ("unknown Provision node type", "provision", unknown_node_type),
        ("legacy proviso node type", "provision", legacy_proviso),
        ("legacy document type", "act", legacy_document_type),
        ("duplicated Act language", "act", duplicated_language),
        ("unknown Act status", "act", unknown_act_status),
        ("multiple authoritative Sources", "act", duplicate_authority),
        ("Act-owned Source", "source", owned_source),
        ("non-PDF Source", "source", html_source),
        ("Citation workflow field", "citation", workflow_citation),
        ("blank table cell with content", "table", invalid_table),
        ("null printed page", "provision", null_printed_page),
    ]
    for name, record_type, record in invalid_records:
        if validators[record_type].is_valid(record):
            raise AssertionError(f"{name} should be rejected")

    constitution_for_corpus = copy.deepcopy(constitution)
    constitution_for_corpus["source_refs"][0]["source_id"] = fixtures["source"][
        "source_id"
    ]
    table_provision = copy.deepcopy(fixtures["provision"])
    table_provision.update(
        {
            "provision_id": "ng-federal-act-2023-37:schedule-1.table-1",
            "node_type": "table",
            "display_label": "TABLE",
            "heading": "Example fees",
            "order": 2,
            "source_spans": copy.deepcopy(fixtures["table"]["source_spans"]),
            "content_blocks": [copy.deepcopy(fixtures["table"])],
        }
    )
    corpus = {
        "acts": [fixtures["act"], constitution_for_corpus],
        "provisions": [fixtures["provision"], table_provision],
        "sources": [fixtures["source"]],
        "citations": [fixtures["citation"]],
    }
    validate_corpus(**corpus)

    reversed_range = copy.deepcopy(corpus)
    reversed_range["citations"][0]["text_range"] = {"start": 13, "end": 4}
    assert_invalid_corpus("reversed Citation range", **reversed_range)

    mismatched_target = copy.deepcopy(corpus)
    mismatched_target["citations"][0]["target"]["act_id"] = constitution["act_id"]
    assert_invalid_corpus("mismatched Citation target", **mismatched_target)

    incomplete_order = copy.deepcopy(corpus)
    incomplete_order["provisions"][1]["order"] = 3
    assert_invalid_corpus("incomplete Provision order", **incomplete_order)

    parent_cycle = copy.deepcopy(corpus)
    parent_cycle["provisions"][0]["parent_provision_id"] = parent_cycle["provisions"][
        1
    ]["provision_id"]
    parent_cycle["provisions"][1]["parent_provision_id"] = parent_cycle["provisions"][
        0
    ]["provision_id"]
    parent_cycle["provisions"][1]["order"] = 1
    assert_invalid_corpus("Provision parent cycle", **parent_cycle)

    missing_source_page = copy.deepcopy(corpus)
    missing_source_page["provisions"][0]["source_spans"][0]["pdf_page"] = 3
    assert_invalid_corpus("out-of-range Source page", **missing_source_page)

    duplicate_block = copy.deepcopy(corpus)
    duplicate_block["provisions"][0]["content_blocks"].append(
        copy.deepcopy(duplicate_block["provisions"][0]["content_blocks"][0])
    )
    assert_invalid_corpus("duplicate content block ID", **duplicate_block)

    overflowing_table = copy.deepcopy(fixtures["table"])
    overflowing_table["row_groups"][0]["rows"][0]["cells"][1]["column_span"] = 2
    try:
        validate_table(overflowing_table)
    except AssertionError:
        pass
    else:
        raise AssertionError("overflowing table cell should be rejected")


if __name__ == "__main__":
    test_contract()
    print("Contract test passed.")
