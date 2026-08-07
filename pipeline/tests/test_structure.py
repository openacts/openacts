import json
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

import openacts_pipeline.structure as structure_module
from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import StructureSettings
from openacts_pipeline.structure import ModelRun, structure
from openacts_pipeline.structure_schema import FocusPlan, StructureDraft

SOURCE_ID = "sha256:" + "a" * 64


def test_deepseek_profile_uses_provider_max_tokens_field() -> None:
    assert (
        structure_module.DEEPSEEK_PROFILE[
            "openai_chat_supports_max_completion_tokens"
        ]
        is False
    )


def _node(
    node_type: str,
    label: str | None = None,
    *children: dict,
    text: str | None = None,
) -> dict:
    return {
        "node_type": node_type,
        "display_label": label,
        "pdf_page": 1,
        "children": list(children),
        "content_blocks": (
            [{"kind": "text", "text": text, "pdf_pages": [1]}] if text else []
        ),
    }


@pytest.mark.parametrize("extraction_version", [1, 2])
def test_structure_dry_run_does_not_call_the_model_or_write(
    tmp_path: Path, extraction_version: int
) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = cache_root / "extractions/extract.json"
    extraction.parent.mkdir(parents=True)
    extraction.write_text(
        json.dumps(
            {
                "stage": "extract",
                "status": "success",
                "extraction_version": extraction_version,
                "source_id": SOURCE_ID,
                "page_count": 1,
                "pages": [
                    {
                        "pdf_page": 1,
                        "text": "raw legal text",
                        "text_characters": 14,
                    }
                ],
                "result_path": "extractions/extract.json",
            }
        ),
        encoding="utf-8",
    )

    def forbidden(*_: object) -> ModelRun:
        raise AssertionError("dry run called the model")

    result = structure(
        extraction,
        cache_root=cache_root,
        primary_runner=forbidden,
    )

    assert result["status"] == "dry_run"
    assert result["network_access"] is False
    assert not (cache_root / "structures").exists()
    assert not (cache_root / "structure-work").exists()


def test_source_validation_can_request_one_model_correction(monkeypatch) -> None:
    model = TestModel(
        custom_output_args={
            "units": [
                {
                    "kind": "part",
                    "display_label": "PART I",
                    "heading": "OBJECTIVES",
                    "pdf_page": 1,
                }
            ]
        }
    )
    monkeypatch.setattr(structure_module, "_model", lambda *_: model)
    attempts = 0

    def validate(_: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PipelineError(
                "source_text_mismatch",
                "part display label 'PART II' is not recoverable from PDF page 9",
            )

    run = structure_module._run_agent(
        "--- PDF PAGE 1 ---\nPART I — OBJECTIVES",
        StructureSettings("key", "https://example.com", "model", 30),
        "Return Part I.",
        FocusPlan,
        validate,
    )

    assert attempts == 2
    assert run.usage["requests"] == 2


def test_schedule_validation_accepts_deep_legal_hierarchy() -> None:
    page_text = """SECOND SCHEDULE
PART I
1. Root wording
(1) First subdivision
(a) Alpha subdivision
(i) Roman wording."""

    draft = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "schedule",
                    "SECOND SCHEDULE",
                    _node(
                        "schedule_part",
                        "PART I",
                        _node(
                            "schedule_paragraph",
                            "1.",
                            _node(
                                "schedule_subparagraph",
                                "(1)",
                                _node(
                                    "paragraph",
                                    "(a)",
                                    _node(
                                        "subparagraph",
                                        "(i)",
                                        text="Roman wording.",
                                    ),
                                ),
                            ),
                        ),
                    ),
                )
            ]
        }
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.SCHEDULE_NODE_TYPES,
        page_count=1,
        pages=[{"pdf_page": 1, "text": page_text}],
    )


def test_definition_clauses_must_be_structured_as_paragraphs() -> None:
    page_text = """65. Interpretation
"competent authority" includes —
(a) the Government of the Federal Republic of Nigeria ; or
(b) any state government or statutory authority ;"""
    flattened = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "section",
                    "65.",
                    _node(
                        "definition",
                        text=(
                            '"competent authority" includes — (a) the Government of '
                            "the Federal Republic of Nigeria ; or (b) any state "
                            "government or statutory authority ;"
                        ),
                    ),
                )
            ]
        }
    )

    with pytest.raises(PipelineError, match="addressable marker"):
        structure_module._validate_draft(
            flattened,
            allowed_types=structure_module.PART_NODE_TYPES,
            page_count=1,
            pages=[{"pdf_page": 1, "text": page_text}],
        )

    structured = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "section",
                    "65.",
                    _node(
                        "definition",
                        None,
                        _node(
                            "paragraph",
                            "(a)",
                            text="the Government of the Federal Republic of Nigeria ; or",
                        ),
                        _node(
                            "paragraph",
                            "(b)",
                            text="any state government or statutory authority ;",
                        ),
                        text='"competent authority" includes —',
                    ),
                )
            ]
        }
    )

    structure_module._validate_draft(
        structured,
        allowed_types=structure_module.PART_NODE_TYPES,
        page_count=1,
        pages=[{"pdf_page": 1, "text": page_text}],
    )
