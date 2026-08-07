#!/usr/bin/env python3
"""Test the OpenActs Gate A data contract."""

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, RefResolver
from openacts_pipeline.corpus import validate_corpus, validate_table

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
