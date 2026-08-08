"""Model-facing Provision drafts and canonical content materialization."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, model_validator
from referencing import Registry, Resource

from openacts_pipeline.common import PipelineError

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schemas"


def _load_canonical_node_types() -> tuple[str, ...]:
    schema_path = SCHEMA_DIR / "provision.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        values = schema["properties"]["node_type"]["enum"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"cannot load canonical node types: {exc}") from exc
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) and value for value in values)
    ):
        raise RuntimeError("canonical node_type enum must contain strings")
    return tuple(values)


def _content_validator() -> Draft202012Validator:
    registry = Registry().with_resources(
        (
            path.resolve().as_uri(),
            Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))),
        )
        for path in SCHEMA_DIR.glob("*.schema.json")
    )
    return Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "array",
            "items": {
                "$ref": (SCHEMA_DIR / "common.schema.json").resolve().as_uri()
                + "#/$defs/content_block"
            },
        },
        registry=registry,
        format_checker=FormatChecker(),
    )


CANONICAL_NODE_TYPES = _load_canonical_node_types()
NodeType = Literal[*CANONICAL_NODE_TYPES]  # pyright: ignore[reportInvalidTypeForm]
CONTENT_VALIDATOR = _content_validator()

MarkerStyle = Literal[
    "decimal",
    "lower_alpha",
    "upper_alpha",
    "lower_roman",
    "upper_roman",
    "bullet",
    "none",
    "source",
]
LayoutStatus = Literal[
    "faithfully_reconstructed", "reconstruction_uncertain", "source_conflict"
]


def _validate_pdf_pages(pdf_pages: list[int]) -> None:
    if any(isinstance(page, bool) or page < 1 for page in pdf_pages):
        raise ValueError("pdf_pages must contain positive integers")
    if len(pdf_pages) != len(set(pdf_pages)) or pdf_pages != sorted(pdf_pages):
        raise ValueError("pdf_pages must be unique and increasing")


class DraftTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "quoted_text", "formula", "signature"]
    text: str = Field(min_length=1)
    pdf_pages: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_text(self) -> DraftTextBlock:
        if not self.text.strip():
            raise ValueError("text must not be blank")
        _validate_pdf_pages(self.pdf_pages)
        return self


class DraftListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    content_blocks: list[DraftContentBlock] = Field(min_length=1)
    pdf_pages: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pages(self) -> DraftListItem:
        _validate_pdf_pages(self.pdf_pages)
        return self


class DraftListBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["list"]
    marker_style: MarkerStyle
    start: int | None = Field(default=None, ge=1)
    items: list[DraftListItem] = Field(min_length=1)
    pdf_pages: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pages(self) -> DraftListBlock:
        _validate_pdf_pages(self.pdf_pages)
        return self


class DraftTableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["table"]
    caption: str | None = None
    caption_pdf_pages: list[int] = Field(default_factory=list)
    column_count: int = Field(ge=1)
    header_row_count: int = Field(default=1, ge=0)
    rows: list[list[str | None]] = Field(min_length=1)
    layout_status: LayoutStatus = "faithfully_reconstructed"
    pdf_pages: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_table(self) -> DraftTableBlock:
        _validate_pdf_pages(self.pdf_pages)
        if self.caption is None and self.caption_pdf_pages:
            raise ValueError("caption pages require a caption")
        if self.caption is not None:
            if not self.caption.strip() or not self.caption_pdf_pages:
                raise ValueError("caption requires text and PDF pages")
            _validate_pdf_pages(self.caption_pdf_pages)
        if self.header_row_count > len(self.rows):
            raise ValueError("header_row_count exceeds table rows")
        for row in self.rows:
            if len(row) != self.column_count:
                raise ValueError("every table row must match column_count")
            if any(cell is not None and not cell.strip() for cell in row):
                raise ValueError("use null for blank table cells")
        numbered_headers = {
            int(match.group(1))
            for row in self.rows
            for cell in row
            if cell is not None
            if (match := re.match(r"\s*\((\d+)\)\s+", cell)) is not None
        }
        if len(numbered_headers) > 1:
            expected = set(range(1, max(numbered_headers) + 1))
            if numbered_headers == expected and self.column_count != len(expected):
                raise ValueError("numbered table headers must occupy separate columns")
        return self


DraftContentBlock = Annotated[
    DraftTextBlock | DraftListBlock | DraftTableBlock,
    Field(discriminator="kind"),
]

DraftListItem.model_rebuild()
DraftListBlock.model_rebuild()
DraftTableBlock.model_rebuild()


class DraftNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: NodeType  # pyright: ignore[reportInvalidTypeForm]
    display_label: str | None = None
    heading: str | None = None
    pdf_page: int = Field(ge=1)
    content_blocks: list[DraftContentBlock] = Field(default_factory=list)
    children: list[DraftNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_visible_content(self) -> DraftNode:
        if not self.display_label and not self.heading and not self.content_blocks:
            raise ValueError("node requires a label, heading, or content")
        return self


class StructureDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[DraftNode]


DraftNode.model_rebuild()


class StructureUnit(BaseModel):
    """One bounded, independently replaceable model task."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    kind: Literal["front_matter", "body", "chapter", "part", "schedule"]
    display_label: str | None = None
    heading: str | None = None
    start_pdf_page: int = Field(ge=1)
    end_pdf_page: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_unit(self) -> StructureUnit:
        if self.end_pdf_page < self.start_pdf_page:
            raise ValueError("end_pdf_page must not precede start_pdf_page")
        if self.kind in {"chapter", "part", "schedule"} and not (
            self.display_label or self.heading
        ):
            raise ValueError(f"{self.kind} unit requires a label or heading")
        return self


class StructurePlan(BaseModel):
    """The planner's complete legal scope and semantic execution units."""

    model_config = ConfigDict(extra="forbid")

    legal_start_pdf_page: int = Field(ge=1)
    legal_end_pdf_page: int = Field(ge=1)
    units: list[StructureUnit] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> StructurePlan:
        if self.legal_end_pdf_page < self.legal_start_pdf_page:
            raise ValueError("legal page range is reversed")
        return self


RepairAction = Literal[
    "replace_unit",
    "replan_document",
    "abort_unresolved",
]


class RepairDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    action: RepairAction
    reason: str = Field(min_length=1)


class RepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[RepairDecision] = Field(min_length=1)


def iter_block_texts(
    block: DraftContentBlock,
) -> Iterable[tuple[str, list[int], str]]:
    if isinstance(block, DraftTextBlock):
        yield block.text, block.pdf_pages, "content text"
    elif isinstance(block, DraftListBlock):
        for item in block.items:
            if item.label:
                yield item.label, item.pdf_pages, "list label"
            for child in item.content_blocks:
                yield from iter_block_texts(child)
    else:
        if block.caption:
            yield block.caption, block.caption_pdf_pages, "table caption"
        for row in block.rows:
            for cell in row:
                if cell is not None:
                    yield cell, block.pdf_pages, "table cell"


def iter_block_pages(block: DraftContentBlock) -> Iterable[int]:
    yield from block.pdf_pages
    if isinstance(block, DraftListBlock):
        for item in block.items:
            yield from item.pdf_pages
            for child in item.content_blocks:
                yield from iter_block_pages(child)
    elif isinstance(block, DraftTableBlock):
        yield from block.caption_pdf_pages


def validate_table(table: DraftTableBlock) -> None:
    if any(len(row) != table.column_count for row in table.rows):
        raise PipelineError(
            "invalid_structure_output", "table rows must match column_count"
        )


def _source_spans(source_id: str, pdf_pages: Iterable[int]) -> list[dict[str, Any]]:
    return [
        {"source_id": source_id, "pdf_page": page} for page in sorted(set(pdf_pages))
    ]


def _materialize_blocks(
    blocks: list[DraftContentBlock],
    *,
    source_id: str,
    prefix: str = "",
) -> list[dict[str, Any]]:
    materialized: list[dict[str, Any]] = []
    for block_index, block in enumerate(blocks, start=1):
        block_id = f"{prefix + '-' if prefix else ''}block-{block_index}"
        if isinstance(block, DraftTextBlock):
            materialized.append(
                {
                    "block_id": block_id,
                    "kind": block.kind,
                    "text": block.text,
                    "source_spans": _source_spans(source_id, block.pdf_pages),
                }
            )
            continue
        if isinstance(block, DraftListBlock):
            items = []
            for item_index, item in enumerate(block.items, start=1):
                item_id = f"{block_id}-item-{item_index}"
                items.append(
                    {
                        "item_id": item_id,
                        "label": item.label,
                        "content_blocks": _materialize_blocks(
                            item.content_blocks,
                            source_id=source_id,
                            prefix=item_id,
                        ),
                        "source_spans": _source_spans(source_id, item.pdf_pages),
                    }
                )
            materialized.append(
                {
                    "block_id": block_id,
                    "kind": "list",
                    "marker_style": block.marker_style,
                    "start": block.start,
                    "items": items,
                    "source_spans": _source_spans(source_id, block.pdf_pages),
                }
            )
            continue

        # ponytail: model-facing matrices assume unmerged cells; add spans when a
        # source document actually requires them.
        header_cell_ids = {
            column: [
                f"{block_id}-cell-{row + 1}-{column + 1}"
                for row in range(block.header_row_count)
                if block.rows[row][column] is not None
            ]
            for column in range(block.column_count)
        }
        materialized_rows = []
        for row_index, row in enumerate(block.rows):
            role = "header" if row_index < block.header_row_count else "data"
            cells = []
            for column_index, text in enumerate(row):
                cell_id = f"{block_id}-cell-{row_index + 1}-{column_index + 1}"
                cells.append(
                    {
                        "cell_id": cell_id,
                        "column_start": column_index + 1,
                        "role": role,
                        "scope": "column" if role == "header" else None,
                        "row_span": 1,
                        "column_span": 1,
                        "header_cell_ids": (
                            [] if role == "header" else header_cell_ids[column_index]
                        ),
                        "blank": text is None,
                        "content_blocks": (
                            []
                            if text is None
                            else [
                                {
                                    "block_id": f"{cell_id}-block-1",
                                    "kind": "text",
                                    "text": text,
                                    "source_spans": _source_spans(
                                        source_id, block.pdf_pages
                                    ),
                                }
                            ]
                        ),
                        "source_spans": _source_spans(source_id, block.pdf_pages),
                    }
                )
            materialized_rows.append(
                {"row_id": f"{block_id}-row-{row_index + 1}", "cells": cells}
            )
        groups = []
        if block.header_row_count:
            groups.append(
                {
                    "group_id": f"{block_id}-group-1",
                    "role": "header",
                    "rows": materialized_rows[: block.header_row_count],
                }
            )
        if block.header_row_count < len(materialized_rows):
            groups.append(
                {
                    "group_id": f"{block_id}-group-{len(groups) + 1}",
                    "role": "body",
                    "rows": materialized_rows[block.header_row_count :],
                }
            )
        materialized.append(
            {
                "block_id": block_id,
                "kind": "table",
                "caption": (
                    {
                        "text": block.caption,
                        "source_spans": _source_spans(
                            source_id, block.caption_pdf_pages
                        ),
                    }
                    if block.caption is not None
                    else None
                ),
                "column_count": block.column_count,
                "row_groups": groups,
                "notes": [],
                "source_segments": [],
                "layout_status": block.layout_status,
                "source_spans": _source_spans(source_id, block.pdf_pages),
            }
        )
    return materialized


def _content_characters(blocks: list[dict[str, Any]]) -> int:
    total = 0
    for block in blocks:
        if block["kind"] in {"text", "quoted_text", "formula", "signature"}:
            total += len(block["text"])
        elif block["kind"] == "list":
            for item in block["items"]:
                total += _content_characters(item["content_blocks"])
        else:
            if block["caption"] is not None:
                total += len(block["caption"]["text"])
            for group in block["row_groups"]:
                for row in group["rows"]:
                    for cell in row["cells"]:
                        total += _content_characters(cell["content_blocks"])
            total += _content_characters(block["notes"])
    return total


def materialize_provisions(
    drafts: list[StructureDraft], *, source_id: str
) -> tuple[list[dict[str, Any]], int]:
    proposals: list[tuple[DraftNode, int | None]] = []

    def append_node(node: DraftNode, parent_index: int | None) -> None:
        index = len(proposals)
        proposals.append((node, parent_index))
        for child in node.children:
            append_node(child, index)

    for draft in drafts:
        for node in draft.nodes:
            append_node(node, None)

    sibling_orders: Counter[str | None] = Counter()
    provisions: list[dict[str, Any]] = []
    content_characters = 0
    for index, (node, parent_index) in enumerate(proposals, start=1):
        draft_id = f"node-{index:04d}"
        parent_id = f"node-{parent_index + 1:04d}" if parent_index is not None else None
        sibling_orders[parent_id] += 1
        content_blocks = _materialize_blocks(node.content_blocks, source_id=source_id)
        errors = sorted(
            CONTENT_VALIDATOR.iter_errors(content_blocks),
            key=lambda error: list(error.path),
        )
        if errors:
            error = errors[0]
            field = ".".join(str(part) for part in error.path) or "content_blocks"
            raise PipelineError(
                "invalid_content_blocks", f"{draft_id}.{field}: {error.message}"
            )
        content_characters += _content_characters(content_blocks)
        provisions.append(
            {
                "draft_id": draft_id,
                "node_type": node.node_type,
                "display_label": node.display_label,
                "heading": node.heading,
                "parent_draft_id": parent_id,
                "order": sibling_orders[parent_id],
                "source_spans": _source_spans(source_id, [node.pdf_page]),
                "content_blocks": content_blocks,
                "text_fidelity": "machine_extracted",
            }
        )
    return provisions, content_characters
