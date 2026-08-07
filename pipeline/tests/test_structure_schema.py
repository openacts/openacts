import json
from pathlib import Path

from openacts_pipeline.structure_schema import (
    CANONICAL_NODE_TYPES,
    DraftNode,
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
