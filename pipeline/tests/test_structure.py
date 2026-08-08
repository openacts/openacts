import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.test import TestModel

import openacts_pipeline.structure as structure_module
from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import StructureSettings
from openacts_pipeline.structure import ModelRun, structure
from openacts_pipeline.structure_schema import (
    RepairPlan,
    StructureDraft,
    StructurePlan,
    StructureUnit,
)

SOURCE_ID = "sha256:" + "a" * 64


def test_deepseek_profile_uses_provider_max_tokens_field() -> None:
    assert (
        structure_module.DEEPSEEK_PROFILE["openai_chat_supports_max_completion_tokens"]
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


def _write_extraction(
    cache_root: Path, text: str, *, extraction_version: int = 2
) -> Path:
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
                        "text": text,
                        "text_characters": len(text),
                    }
                ],
                "result_path": "extractions/extract.json",
            }
        ),
        encoding="utf-8",
    )
    return extraction


@pytest.mark.parametrize("extraction_version", [1, 2])
def test_structure_dry_run_does_not_call_the_model_or_write(
    tmp_path: Path, extraction_version: int
) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = _write_extraction(
        cache_root,
        "raw legal text",
        extraction_version=extraction_version,
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


def test_structure_execute_plans_and_audits_bounded_units(tmp_path: Path) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = _write_extraction(cache_root, "1. Supremacy\nBinding wording.")
    calls: list[tuple[str, type[object]]] = []
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 1,
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                }
            ],
        }
    )
    output = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    **_node("section", "1.", text="Binding wording."),
                    "heading": "Supremacy",
                }
            ]
        }
    )
    progress: list[dict[str, object]] = []

    async def runner(
        raw_text: str,
        _: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        calls.append((scope, output_type))
        assert "--- PDF PAGE 1 ---" in raw_text
        assert callable(validate)
        response = plan if output_type is StructurePlan else output
        validate(response)
        return ModelRun(
            output=response,
            model="model",
            output_mode="tool",
            latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    result = structure(
        extraction,
        execute=True,
        cache_root=cache_root,
        settings=StructureSettings("key", "https://example.com", "model", 30),
        primary_runner=runner,
        progress=progress.append,
    )

    assert result["status"] == "success"
    assert [output_type for _, output_type in calls] == [StructurePlan, StructureDraft]
    assert result["summary"]["units"] == 1
    assert (
        result["summary"]["claimed_source_characters"]
        == result["summary"]["source_characters"]
    )
    assert list((cache_root / "structure-work").glob("*/000-plan.json"))
    assert list((cache_root / "structure-work").glob("*/001-body.json"))
    assert [event["event"] for event in progress] == [
        "structure_started",
        "plan_started",
        "plan_completed",
        "units_started",
        "unit_started",
        "unit_completed",
        "units_completed",
        "audit_completed",
        "graph_completed",
        "materialized_audit_completed",
        "structure_completed",
    ]


def test_plan_cannot_exclude_pages_with_addressable_legal_markers() -> None:
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 1,
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                }
            ],
        }
    )

    with pytest.raises(PipelineError, match="outside the planned legal range"):
        structure_module._validate_plan(
            plan,
            [
                {"pdf_page": 1, "text": "1. Included\nComplete wording."},
                {"pdf_page": 2, "text": "(1) Wrongly excluded wording."},
            ],
        )


def test_plan_reports_range_and_source_identity_errors_together() -> None:
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 3,
            "units": [
                {
                    "unit_id": "front-matter",
                    "kind": "front_matter",
                    "heading": "ACT",
                    "start_pdf_page": 2,
                    "end_pdf_page": 2,
                },
                {
                    "unit_id": "schedule-01",
                    "kind": "schedule",
                    "display_label": "FIRST SCHEDULE",
                    "heading": "States; Definition of Area Councils",
                    "start_pdf_page": 3,
                    "end_pdf_page": 3,
                },
            ],
        }
    )

    with pytest.raises(PipelineError) as raised:
        structure_module._validate_plan(
            plan,
            [
                {"pdf_page": 1, "text": "ARRANGEMENT OF SECTIONS"},
                {"pdf_page": 2, "text": "ACT"},
                {"pdf_page": 3, "text": "FIRST SCHEDULE\nStates"},
            ],
        )

    message = str(raised.value)
    assert "excluded arrangement of sections on PDF page 1" in message
    assert (
        "legal_start_pdf_page 1 does not match first unit start_pdf_page 2" in message
    )
    assert "schedule-01 heading 'States; Definition of Area Councils'" in message


def test_plan_canonicalizes_a_shared_boundary_page() -> None:
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                {
                    "unit_id": "chapter-01",
                    "kind": "chapter",
                    "display_label": "CHAPTER I",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                },
                {
                    "unit_id": "chapter-02",
                    "kind": "chapter",
                    "display_label": "CHAPTER II",
                    "start_pdf_page": 2,
                    "end_pdf_page": 2,
                },
            ],
        }
    )

    structure_module._validate_plan(
        plan,
        [
            {"pdf_page": 1, "text": "CHAPTER I\n1. First\nOpening wording."},
            {
                "pdf_page": 2,
                "text": "(3) Continued wording from Chapter I.\nCHAPTER II\n2. Second",
            },
        ],
    )

    assert plan.units[0].end_pdf_page == 2


def test_structure_units_run_in_parallel_and_resume_from_checkpoints(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = cache_root / "extractions/extract.json"
    extraction.parent.mkdir(parents=True)
    pages = [
        {"pdf_page": 1, "text": "1. First\nFirst wording."},
        {"pdf_page": 2, "text": "2. Second\nSecond wording."},
    ]
    extraction.write_text(
        json.dumps(
            {
                "stage": "extract",
                "status": "success",
                "extraction_version": 2,
                "source_id": SOURCE_ID,
                "page_count": 2,
                "pages": [
                    {**page, "text_characters": len(page["text"])} for page in pages
                ],
                "result_path": "extractions/extract.json",
            }
        ),
        encoding="utf-8",
    )
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                {
                    "unit_id": "body-01",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                },
                {
                    "unit_id": "body-02",
                    "kind": "body",
                    "start_pdf_page": 2,
                    "end_pdf_page": 2,
                },
            ],
        }
    )
    active = 0
    max_active = 0

    async def runner(
        raw_text: str,
        _: StructureSettings,
        __: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal active, max_active
        if output_type is StructurePlan:
            output = plan
        else:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            page = 2 if "PDF PAGE 2" in raw_text else 1
            output = StructureDraft.model_validate(
                {
                    "nodes": [
                        {
                            "node_type": "section",
                            "display_label": f"{page}.",
                            "heading": "Second" if page == 2 else "First",
                            "pdf_page": page,
                            "content_blocks": [
                                {
                                    "kind": "text",
                                    "text": (
                                        "Second wording."
                                        if page == 2
                                        else "First wording."
                                    ),
                                    "pdf_pages": [page],
                                }
                            ],
                        }
                    ]
                }
            )
            active -= 1
        assert callable(validate)
        validate(output)
        return ModelRun(
            output=output,
            model="model",
            output_mode="tool",
            latency_seconds=0.05,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    first = structure(
        extraction,
        execute=True,
        cache_root=cache_root,
        settings=StructureSettings("key", "https://example.com", "model", 30),
        primary_runner=runner,
    )

    async def forbidden(*_: object) -> ModelRun:
        raise AssertionError("resume called the model")

    resumed = structure(
        extraction,
        execute=True,
        cache_root=cache_root,
        settings=StructureSettings("key", "https://example.com", "model", 30),
        primary_runner=forbidden,
    )

    assert first["status"] == "success"
    assert max_active == 2
    assert resumed["summary"]["checkpoints_reused"] == 3


def test_replan_uses_new_unit_checkpoint_namespace(tmp_path: Path) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = cache_root / "extractions/extract.json"
    extraction.parent.mkdir(parents=True)
    pages = [
        {"pdf_page": 1, "text": "1. First\nFirst wording."},
        {"pdf_page": 2, "text": "2. Second\nSecond wording."},
    ]
    extraction.write_text(
        json.dumps(
            {
                "stage": "extract",
                "status": "success",
                "extraction_version": 2,
                "source_id": SOURCE_ID,
                "page_count": 2,
                "pages": [
                    {**page, "text_characters": len(page["text"])} for page in pages
                ],
                "result_path": "extractions/extract.json",
            }
        ),
        encoding="utf-8",
    )
    initial_plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                {
                    "unit_id": "body-01",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                },
                {
                    "unit_id": "body-02",
                    "kind": "body",
                    "start_pdf_page": 2,
                    "end_pdf_page": 2,
                },
            ],
        }
    )
    revised_plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                {
                    "unit_id": "body-01",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 2,
                }
            ],
        }
    )
    first = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    **_node("section", "1.", text="First wording."),
                    "heading": "First",
                }
            ]
        }
    )
    complete = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    **_node("section", "1.", text="First wording."),
                    "heading": "First",
                },
                {
                    "node_type": "section",
                    "display_label": "2.",
                    "heading": "Second",
                    "pdf_page": 2,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "Second wording.",
                            "pdf_pages": [2],
                        }
                    ],
                },
            ]
        }
    )
    plan_calls = 0

    async def runner(
        raw_text: str,
        _: StructureSettings,
        __: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal plan_calls
        assert callable(validate)
        if output_type is StructurePlan:
            plan_calls += 1
            output = initial_plan if plan_calls == 1 else revised_plan
        elif output_type is RepairPlan:
            output = RepairPlan.model_validate(
                {
                    "decisions": [
                        {
                            "unit_id": "body-02",
                            "action": "replan_document",
                            "reason": "the original unit boundary was wrong",
                        }
                    ]
                }
            )
        elif "PDF PAGE 1" in raw_text and "PDF PAGE 2" in raw_text:
            output = complete
        elif "PDF PAGE 1" in raw_text:
            output = first
        else:
            output = StructureDraft(nodes=[])
        validate(output)
        return ModelRun(
            output=output,
            model="model",
            output_mode="tool",
            latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    result = structure(
        extraction,
        execute=True,
        cache_root=cache_root,
        settings=StructureSettings("key", "https://example.com", "model", 30),
        primary_runner=runner,
    )

    assert result["status"] == "success"
    assert result["summary"]["repair_rounds"] == 1
    assert list((cache_root / "structure-work").glob("*/001-body-01.json"))
    assert list((cache_root / "structure-work").glob("*/101-body-01.json"))


def test_rejected_proviso_draft_is_preserved_and_repaired(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "source-cache"
    page_text = """68. Tenure of seat
(1) A member shall vacate his seat if —
(g) he becomes a member of another political party;
Provided that his membership resulted from a party division; or
(h) he is recalled."""
    extraction = _write_extraction(cache_root, page_text)
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 1,
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                }
            ],
        }
    )
    rejected = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    **_node(
                        "section",
                        "68.",
                        _node(
                            "subsection",
                            "(1)",
                            _node(
                                "paragraph",
                                "(g)",
                                text="he becomes a member of another political party;",
                            ),
                            _node(
                                "paragraph",
                                text=(
                                    "Provided that his membership resulted from a party "
                                    "division; or"
                                ),
                            ),
                            _node("paragraph", "(h)", text="he is recalled."),
                            text="A member shall vacate his seat if —",
                        ),
                    ),
                    "heading": "Tenure of seat",
                }
            ]
        }
    )
    repaired = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    **_node(
                        "section",
                        "68.",
                        _node(
                            "subsection",
                            "(1)",
                            {
                                **_node(
                                    "paragraph",
                                    "(g)",
                                    text=(
                                        "he becomes a member of another political party;"
                                    ),
                                ),
                                "content_blocks": [
                                    {
                                        "kind": "text",
                                        "text": (
                                            "he becomes a member of another political "
                                            "party;"
                                        ),
                                        "pdf_pages": [1],
                                    },
                                    {
                                        "kind": "text",
                                        "text": (
                                            "Provided that his membership resulted from "
                                            "a party division; or"
                                        ),
                                        "pdf_pages": [1],
                                    },
                                ],
                            },
                            _node("paragraph", "(h)", text="he is recalled."),
                            text="A member shall vacate his seat if —",
                        ),
                    ),
                    "heading": "Tenure of seat",
                }
            ]
        }
    )

    draft_calls = 0

    async def runner(
        _: str,
        settings: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal draft_calls
        assert callable(validate)
        if output_type is StructurePlan:
            validate(plan)
            return ModelRun(
                output=plan,
                model="model",
                output_mode="tool",
                latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        if output_type is RepairPlan:
            repair_plan = RepairPlan.model_validate(
                {
                    "decisions": [
                        {
                            "unit_id": "body",
                            "action": "replace_unit",
                            "reason": "unnumbered proviso is a local ownership error",
                        }
                    ]
                }
            )
            validate(repair_plan)
            return ModelRun(
                output=repair_plan,
                model="model",
                output_mode="tool",
                latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        draft_calls += 1
        if draft_calls == 2:
            raise PipelineError(
                "model_transient",
                "temporary provider failure during repair",
                retryable=True,
            )
        output = rejected if draft_calls == 1 else repaired
        try:
            validate(output)
        except PipelineError as exc:
            raise structure_module._RejectedModelOutput(
                settings.primary_model, output, exc
            ) from exc
        assert "PREVIOUS DRAFT" in scope
        return ModelRun(
            output=output,
            model="model",
            output_mode="tool",
            latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    result = structure(
        extraction,
        execute=True,
        cache_root=cache_root,
        settings=StructureSettings("key", "https://example.com", "model", 30),
        primary_runner=runner,
    )

    [failure_path] = list(
        (cache_root / "structure-work").glob("*/001-body.failure.json")
    )
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["validation_error"]["code"] == "invalid_proviso_structure"
    assert "PDF page 1" in failure["validation_error"]["message"]
    assert "subsection (1)" in failure["validation_error"]["message"]
    assert "paragraph (g)" in failure["validation_error"]["message"]
    assert failure["output"] == rejected.model_dump(mode="json")
    assert result["status"] == "success"
    assert result["summary"]["repair_rounds"] == 1
    assert list(
        (cache_root / "structure-work").glob("*/2101-body-repair-1-retry-1.json")
    )

    async def forbidden(*_: object) -> ModelRun:
        raise AssertionError("repaired resume called the model")

    resumed = structure(
        extraction,
        execute=True,
        cache_root=cache_root,
        settings=StructureSettings("key", "https://example.com", "model", 30),
        primary_runner=forbidden,
    )
    assert resumed["status"] == "success"
    assert resumed["summary"]["checkpoints_reused"] == 4


def test_addressable_list_labels_cannot_bypass_structural_nodes() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Example",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "list",
                            "marker_style": "bullet",
                            "pdf_pages": [1],
                            "items": [
                                {
                                    "label": "(a)",
                                    "pdf_pages": [1],
                                    "content_blocks": [
                                        {
                                            "kind": "text",
                                            "text": "First item.",
                                            "pdf_pages": [1],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    with pytest.raises(PipelineError, match="addressable marker"):
        structure_module._validate_draft(
            draft,
            allowed_types=structure_module.DOCUMENT_NODE_TYPES,
            page_count=1,
            pages=[{"pdf_page": 1, "text": "1. Example\n(a) First item."}],
        )


def test_section_sequence_tracks_lettered_insertions() -> None:
    valid = [
        {"node_type": "section", "display_label": label}
        for label in ("5.", "5A.", "5B.", "6.")
    ]
    structure_module._validate_section_sequence(valid)

    missing_six = [
        {"node_type": "section", "display_label": label}
        for label in ("5.", "5A.", "7.")
    ]
    with pytest.raises(PipelineError, match="5A to 7"):
        structure_module._validate_section_sequence(missing_six)


def test_source_validation_stops_before_repeating_a_full_document(monkeypatch) -> None:
    model = TestModel(
        custom_output_args={"nodes": [_node("section", "1.", text="Binding wording.")]}
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

    with pytest.raises(structure_module._RejectedModelOutput) as caught:
        asyncio.run(
            structure_module._run_agent(
                "--- PDF PAGE 1 ---\n1. Supremacy\nBinding wording.",
                StructureSettings("key", "https://example.com", "model", 30),
                "Return the document.",
                StructureDraft,
                validate,
            )
        )

    assert attempts == 1
    assert caught.value.output.model_dump(mode="json") == StructureDraft.model_validate(
        {"nodes": [_node("section", "1.", text="Binding wording.")]}
    ).model_dump(mode="json")
    assert model.last_model_request_parameters.function_tools == []


def test_agent_usage_limit_becomes_a_pipeline_error(monkeypatch) -> None:
    model = TestModel()

    async def exceed_limit(*_: object, **kwargs: object) -> object:
        usage = kwargs["usage"]
        usage.requests = 1
        usage.input_tokens = 123
        usage.output_tokens = 45
        raise UsageLimitExceeded("request loop")

    monkeypatch.setattr(structure_module.Agent, "run", exceed_limit)

    with pytest.raises(PipelineError) as caught:
        asyncio.run(
            structure_module._run_agent(
                "--- PDF PAGE 1 ---\n1. Example",
                StructureSettings("key", "https://example.com", "model", 30),
                "Plan the document.",
                StructurePlan,
                model=model,
            )
        )

    assert caught.value.code == "model_usage_limit"
    assert caught.value.retryable is False
    assert "after 1 request(s), 123 input tokens, and 45 output tokens" in str(
        caught.value
    )


def test_agent_invalid_output_surfaces_the_validation_cause(monkeypatch) -> None:
    model = TestModel()

    async def invalid_output(*_: object, **__: object) -> object:
        cause = ValueError("nodes.17.children.2: required field is missing")
        raise structure_module.UnexpectedModelBehavior(
            "Exceeded maximum output retries (0)"
        ) from cause

    monkeypatch.setattr(structure_module.Agent, "run", invalid_output)

    with pytest.raises(PipelineError) as caught:
        asyncio.run(
            structure_module._run_agent(
                "--- PDF PAGE 1 ---\n1. Example",
                StructureSettings("key", "https://example.com", "model", 30),
                "Return the document.",
                StructureDraft,
                model=model,
            )
        )

    assert caught.value.code == "model_invalid_output"
    assert "nodes.17.children.2: required field is missing" in str(caught.value)


def test_agent_recovers_only_stray_trailing_json_delimiters(monkeypatch) -> None:
    model = TestModel()
    expected = StructureDraft.model_validate(
        {"nodes": [_node("section", "1.", text="Binding wording.")]}
    )
    malformed = expected.model_dump_json() + "}]}"

    async def trailing_delimiters(*_: object, **kwargs: object) -> object:
        usage = kwargs["usage"]
        usage.requests = 1
        usage.input_tokens = 123
        usage.output_tokens = 456
        try:
            StructureDraft.model_validate_json(malformed)
        except ValidationError as cause:
            raise structure_module.UnexpectedModelBehavior(
                "Exceeded maximum output retries (0)"
            ) from cause
        raise AssertionError("malformed JSON unexpectedly validated")

    monkeypatch.setattr(structure_module.Agent, "run", trailing_delimiters)

    result = asyncio.run(
        structure_module._run_agent(
            "--- PDF PAGE 1 ---\n1. Example",
            StructureSettings("key", "https://example.com", "model", 30),
            "Return the document.",
            StructureDraft,
            model=model,
        )
    )

    assert result.output == expected
    assert result.output_mode == "tool_recovered_trailing_delimiters"
    assert result.usage == {
        "requests": 1,
        "input_tokens": 123,
        "output_tokens": 456,
    }


def test_schedule_validation_accepts_deep_legal_hierarchy() -> None:
    page_text = """SECOND SCHEDULE
PART I
A - General
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
                            "cross_heading",
                            "A - General",
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
                    ),
                )
            ]
        }
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=1,
        pages=[{"pdf_page": 1, "text": page_text}],
    )


def test_schedule_validation_accepts_definitions() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "schedule",
                    "FIFTH SCHEDULE",
                    _node("definition", text='"asset" means property.'),
                )
            ]
        }
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.UNIT_NODE_TYPES["schedule"],
        page_count=1,
        pages=[
            {
                "pdf_page": 1,
                "text": 'FIFTH SCHEDULE\n"asset" means property.',
            }
        ],
    )


def test_schedule_validation_canonicalizes_body_node_names() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "schedule",
                    "SECOND SCHEDULE",
                    _node(
                        "part",
                        "PART I",
                        _node("section", "1.", text="Accounts."),
                    ),
                )
            ]
        }
    )
    target = StructureUnit.model_validate(
        {
            "unit_id": "schedule-02",
            "kind": "schedule",
            "display_label": "SECOND SCHEDULE",
            "start_pdf_page": 1,
            "end_pdf_page": 1,
        }
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.UNIT_NODE_TYPES["schedule"],
        page_count=1,
        pages=[
            {
                "pdf_page": 1,
                "text": "SECOND SCHEDULE\nPART I\n1. Accounts.",
            }
        ],
        target=target,
    )

    part = draft.nodes[0].children[0]
    assert part.node_type == "schedule_part"
    assert part.children[0].node_type == "schedule_paragraph"


def test_cross_heading_can_own_its_sections() -> None:
    page_text = """A - Composition and Staff of National Assembly
47. Establishment of National Assembly
There shall be a National Assembly."""
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "cross_heading",
                    None,
                    _node(
                        "section",
                        "47.",
                        text="There shall be a National Assembly.",
                    ),
                    text="A - Composition and Staff of National Assembly",
                )
            ]
        }
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
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
            allowed_types=structure_module.DOCUMENT_NODE_TYPES,
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
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=1,
        pages=[{"pdf_page": 1, "text": page_text}],
    )
