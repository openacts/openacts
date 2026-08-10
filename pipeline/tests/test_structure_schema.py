import json
from pathlib import Path

from openacts_pipeline.structure_schema import (
    CANONICAL_NODE_TYPES,
    DraftNode,
    DraftTableBlock,
    DraftTextBlock,
    StructureDraft,
    materialize_provisions,
)

SOURCE_ID = "sha256:" + "a" * 64


def test_draft_schema_uses_canonical_types_and_materializes_content() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas/provision.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert CANONICAL_NODE_TYPES == tuple(schema["properties"]["node_type"]["enum"])
    draft_schema = DraftNode.model_json_schema()
    assert draft_schema["$defs"]["DraftNode"]["properties"]["node_type"][
        "enum"
    ] == list(CANONICAL_NODE_TYPES)

    provisions, characters = materialize_provisions(
        [
            StructureDraft(
                nodes=[
                    DraftNode(
                        node_type="part",
                        display_label="PART I",
                        pdf_page=1,
                        children=[
                            DraftNode(
                                node_type="section",
                                display_label="1.",
                                pdf_page=1,
                                content_blocks=[
                                    DraftTextBlock(
                                        kind="text",
                                        text="Exact wording.",
                                        pdf_pages=[1],
                                    )
                                ],
                            )
                        ],
                    )
                ]
            )
        ],
        source_id=SOURCE_ID,
    )

    assert characters == len("Exact wording.")
    assert provisions[1]["parent_draft_id"] == provisions[0]["draft_id"]
    assert provisions[1]["content_blocks"] == [
        {
            "block_id": "block-1",
            "kind": "text",
            "text": "Exact wording.",
            "source_spans": [{"source_id": SOURCE_ID, "pdf_page": 1}],
        }
    ]


def test_draft_node_recovers_table_block_misplaced_in_children() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "schedule",
                    "display_label": "FIRST SCHEDULE",
                    "pdf_page": 217,
                    "children": [
                        {
                            "node_type": "schedule_part",
                            "display_label": "PART I",
                            "pdf_page": 217,
                            "children": [
                                {
                                    "node_type": "table",
                                    "pdf_page": 217,
                                    "kind": "table",
                                    "column_count": 2,
                                    "header_row_count": 1,
                                    "rows": [["State", "Capital"], ["Abia", "Umuahia"]],
                                    "pdf_pages": [217],
                                    "layout_status": "faithfully_reconstructed",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    schedule_part = draft.nodes[0].children[0]
    assert schedule_part.children == []
    assert schedule_part.content_blocks == [
        DraftTableBlock(
            kind="table",
            column_count=2,
            header_row_count=1,
            rows=[["State", "Capital"], ["Abia", "Umuahia"]],
            pdf_pages=[217],
            layout_status="faithfully_reconstructed",
        )
    ]
