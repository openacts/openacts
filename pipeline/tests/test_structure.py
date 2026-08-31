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
    DraftTextBlock,
    RepairPatch,
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
        "candidate_written",
        "graph_completed",
        "materialized_audit_completed",
        "structure_completed",
    ]


def test_failed_audit_writes_an_inspectable_candidate(tmp_path: Path) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = _write_extraction(
        cache_root, "1. Example\nClaimed wording. Missing wording."
    )
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
    incomplete = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Example",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "Claimed wording.",
                            "pdf_pages": [1],
                        }
                    ],
                }
            ]
        }
    )
    progress: list[dict[str, object]] = []

    async def runner(
        _: str,
        __: StructureSettings,
        ___: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        output = plan if output_type is StructurePlan else incomplete
        assert callable(validate)
        validate(output)
        return ModelRun(
            output=output,
            model="model",
            output_mode="tool",
            latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    with pytest.raises(PipelineError, match="candidate.json"):
        structure(
            extraction,
            execute=True,
            cache_root=cache_root,
            settings=StructureSettings(
                "key",
                "https://example.com",
                "model",
                30,
                max_repair_rounds=0,
            ),
            primary_runner=runner,
            progress=progress.append,
        )

    candidate_path = next((cache_root / "structure-work").glob("*/candidate.json"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate["status"] == "incomplete"
    assert candidate["units"][0]["unit_id"] == "body"
    assert candidate["units"][0]["draft"] == incomplete.model_dump(mode="json")
    assert candidate["audit"]["passed"] is False
    assert "candidate_written" in [event["event"] for event in progress]
    assert not (cache_root / "structures").exists()

    resumed_progress: list[dict[str, object]] = []

    async def forbidden(*_: object) -> ModelRun:
        raise AssertionError("candidate resume called the model")

    with pytest.raises(PipelineError, match="candidate.json"):
        structure(
            extraction,
            execute=True,
            cache_root=cache_root,
            settings=StructureSettings(
                "key",
                "https://example.com",
                "model",
                30,
                max_repair_rounds=0,
            ),
            primary_runner=forbidden,
            progress=resumed_progress.append,
        )
    assert "candidate_resumed" in [event["event"] for event in resumed_progress]


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


def test_plan_rejects_a_unit_above_the_character_budget() -> None:
    """An oversized unit returns an incomplete draft or exhausts the timeout."""
    pages = [
        {"pdf_page": page, "text": f"{page}. Heading\n" + "wording " * 900}
        for page in (1, 2)
    ]
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 2,
                }
            ],
        }
    )

    with pytest.raises(PipelineError, match="above the 5000 character limit"):
        structure_module._validate_plan(plan, pages, 5000)

    structure_module._validate_plan(plan, pages, 100_000)
    structure_module._validate_plan(plan, pages)


def test_unit_validation_uses_plan_authoritative_root_metadata() -> None:
    unit = StructureUnit.model_validate(
        {
            "unit_id": "schedule-01",
            "kind": "schedule",
            "display_label": "FIRST SCHEDULE",
            "heading": None,
            "start_pdf_page": 2,
            "end_pdf_page": 2,
        }
    )
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "schedule",
                    "display_label": "FIRST SCHEDULE",
                    "heading": "[Section 3]",
                    "pdf_page": 1,
                    "children": [
                        {
                            "node_type": "part",
                            "display_label": "PART I",
                            "pdf_page": 2,
                            "content_blocks": [
                                {
                                    "kind": "text",
                                    "text": "Exact wording.",
                                    "pdf_pages": [2],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    pages = [
        {"pdf_page": 1, "text": "Cover"},
        {
            "pdf_page": 2,
            "text": "FIRST SCHEDULE\n[Section 3]\nPART I\nExact wording.",
        },
    ]

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.UNIT_NODE_TYPES["schedule"],
        page_count=2,
        pages=pages,
        target=unit,
    )

    assert draft.nodes[0].display_label == "FIRST SCHEDULE"
    assert draft.nodes[0].heading is None
    assert draft.nodes[0].pdf_page == 2
    assert draft.nodes[0].children[0].node_type == "schedule_part"


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


def test_replan_reuses_a_unit_whose_definition_did_not_change(tmp_path: Path) -> None:
    """A replan rewrites the units it changes; re-running the rest buys nothing."""
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
    unchanged = {
        "unit_id": "body-01",
        "kind": "body",
        "start_pdf_page": 1,
        "end_pdf_page": 1,
    }
    initial_plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                unchanged,
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
                unchanged,
                {
                    "unit_id": "body-02b",
                    "kind": "body",
                    "start_pdf_page": 2,
                    "end_pdf_page": 2,
                },
            ],
        }
    )
    first = StructureDraft.model_validate(
        {"nodes": [{**_node("section", "1.", text="First wording."), "heading": "First"}]}
    )
    second = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "2.",
                    "heading": "Second",
                    "pdf_page": 2,
                    "content_blocks": [
                        {"kind": "text", "text": "Second wording.", "pdf_pages": [2]}
                    ],
                }
            ]
        }
    )
    plan_calls = 0
    page_one_calls = 0
    page_two_calls = 0

    async def runner(
        raw_text: str,
        _: StructureSettings,
        __: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal plan_calls, page_one_calls, page_two_calls
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
        elif "First wording." in raw_text:
            page_one_calls += 1
            output = first
        else:
            page_two_calls += 1
            output = StructureDraft(nodes=[]) if page_two_calls == 1 else second
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

    assert result["summary"]["repair_rounds"] == 1
    assert page_one_calls == 1
    work = cache_root / "structure-work"
    assert list(work.glob("*/001-body-01.json"))
    assert not list(work.glob("*/101-body-01.json"))
    assert list(work.glob("*/102-body-02b.json"))


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

    patch_calls = 0

    async def runner(
        _: str,
        settings: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal patch_calls
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
        if output_type is RepairPatch:
            patch_calls += 1
            if patch_calls == 1:
                raise PipelineError(
                    "model_transient",
                    "temporary provider failure during repair",
                    retryable=True,
                )
            output = RepairPatch.model_validate(
                {
                    "unit_id": "body",
                    "operations": [
                        {
                            "op": "move",
                            "from_path": (
                                "/nodes/0/children/0/children/1/content_blocks/0"
                            ),
                            "path": ("/nodes/0/children/0/children/0/content_blocks/-"),
                        },
                        {
                            "op": "remove",
                            "path": "/nodes/0/children/0/children/1",
                        },
                    ],
                }
            )
            validate(output)
            assert "CURRENT DRAFT" in scope
            return ModelRun(
                output=output,
                model="model",
                output_mode="tool",
                latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        assert output_type is StructureDraft
        output = rejected
        try:
            validate(output)
        except PipelineError as exc:
            raise structure_module._RejectedModelOutput(
                settings.primary_model, output, exc
            ) from exc
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
        (cache_root / "structure-work").glob("*/3101-body-patch-repair-1-retry-1.json")
    )
    [candidate_path] = list((cache_root / "structure-work").glob("*/candidate.json"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate["units"][0]["draft"] == repaired.model_dump(mode="json")

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

    with pytest.raises(PipelineError, match="addressable marker") as caught:
        structure_module._validate_draft(
            draft,
            allowed_types=structure_module.DOCUMENT_NODE_TYPES,
            page_count=1,
            pages=[{"pdf_page": 1, "text": "1. Example\n(a) First item."}],
        )
    assert "/nodes/0/content_blocks/0" in str(caught.value)


def test_content_validation_requires_a_contiguous_normalized_source_span() -> None:
    draft = StructureDraft.model_validate(
        {"nodes": [_node("section", "1.", text="alpha beta")]}
    )

    with pytest.raises(PipelineError, match="content text.*not recoverable") as caught:
        structure_module._validate_draft(
            draft,
            allowed_types=structure_module.DOCUMENT_NODE_TYPES,
            page_count=1,
            pages=[
                {
                    "pdf_page": 1,
                    "text": "1. alpha intervening wording beta",
                }
            ],
        )
    assert "/nodes/0/content_blocks/0" in str(caught.value)
    assert "alpha beta" in str(caught.value)


def test_content_validation_ignores_recurring_headers_across_pages() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "Alpha beta.",
                            "pdf_pages": [1, 2],
                        }
                    ],
                }
            ]
        }
    )
    pages = [
        {
            "pdf_page": 1,
            "text": "1\nThe Federal Republic Example Constitution\n1. Alpha",
        },
        {
            "pdf_page": 2,
            "text": "2\nThe Federal Republic Example Constitution\nbeta.",
        },
        {
            "pdf_page": 3,
            "text": "3\nThe Federal Republic Example Constitution\nUnused.",
        },
    ]

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=3,
        pages=pages,
    )


def test_validation_removes_content_already_classified_as_editorial() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "document_title",
                    "display_label": "Example Constitution",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "[Section 7]",
                            "pdf_pages": [1],
                        }
                    ],
                }
            ]
        }
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=1,
        pages=[
            {
                "pdf_page": 1,
                "text": "Example Constitution\n[Section 7]",
            }
        ],
    )

    assert draft.nodes[0].content_blocks == []


def test_validation_recovers_promoted_proviso_and_following_paragraph() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "section",
                    "68.",
                    _node(
                        "subsection",
                        "(1)",
                        _node("paragraph", "(g)", text="Party membership changes;"),
                        text="A member vacates office if —",
                    ),
                    _node(
                        "subsection",
                        text="Provided that a party division caused the change; or",
                    ),
                    _node("subsection", text="(h) the member is recalled."),
                )
            ]
        }
    )
    page_text = (
        "68. A member vacates office if —\n"
        "(1) A member vacates office if —\n"
        "(g) Party membership changes;\n"
        "Provided that a party division caused the change; or\n"
        "(h) the member is recalled."
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=1,
        pages=[{"pdf_page": 1, "text": page_text}],
    )

    section = draft.nodes[0]
    assert len(section.children) == 1
    subsection = section.children[0]
    assert [child.display_label for child in subsection.children] == ["(g)", "(h)"]
    proviso = subsection.children[0].content_blocks[-1]
    assert isinstance(proviso, DraftTextBlock)
    assert proviso.text.startswith("Provided that")
    following = subsection.children[1].content_blocks[0]
    assert isinstance(following, DraftTextBlock)
    assert following.text == "the member is recalled."


def test_validation_relocates_uniquely_grounded_block_and_marker() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "section",
                    "1.",
                    _node("paragraph", "(b)", text="require evidence on oath;"),
                )
            ]
        }
    )
    paragraph = draft.nodes[0].children[0]
    draft.nodes[0].pdf_page = 2
    paragraph.pdf_page = 3
    block = paragraph.content_blocks[0]
    assert isinstance(block, DraftTextBlock)
    block.pdf_pages = [3]

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=3,
        pages=[
            {"pdf_page": 1, "text": "(b) require evidence on oath;"},
            {"pdf_page": 2, "text": "1. Example\n(b) require evidence on oath;"},
            {"pdf_page": 3, "text": "(b) unrelated wording."},
        ],
    )

    assert paragraph.pdf_page == 2
    assert block.pdf_pages == [2]


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


def test_repair_patch_gets_one_bounded_validation_retry(monkeypatch) -> None:
    patch = RepairPatch.model_validate(
        {
            "unit_id": "chapter-01",
            "operations": [
                {
                    "op": "replace",
                    "path": "/nodes/0/pdf_page",
                    "value": 5,
                }
            ],
        }
    )
    model = TestModel(
        custom_output_args=patch.model_dump(mode="json", exclude_none=True)
    )
    monkeypatch.setattr(structure_module, "_model", lambda *_: model)
    attempts = 0

    def validate(_: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PipelineError(
                "invalid_repair_patch",
                "list index 12 does not exist",
            )

    result = asyncio.run(
        structure_module._run_agent(
            "--- PDF PAGE 5 ---\nCHAPTER I",
            StructureSettings("key", "https://example.com", "model", 30),
            "Repair chapter-01.",
            RepairPatch,
            validate,
        )
    )

    assert result.output == patch
    assert attempts == 2
    assert result.usage["requests"] == 2


def test_repair_scope_indexes_exact_candidate_paths() -> None:
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                _node(
                    "chapter",
                    "CHAPTER I",
                    _node("section", "1.", text="Binding wording."),
                )
            ]
        }
    )
    unit = StructureUnit.model_validate(
        {
            "unit_id": "chapter-01",
            "kind": "chapter",
            "display_label": "CHAPTER I",
            "start_pdf_page": 1,
            "end_pdf_page": 1,
        }
    )

    scope = structure_module._repair_scope(unit, [], draft)

    assert "NODE PATH INDEX (copy paths exactly)" in scope
    assert "hidden addressable marker" in scope
    assert (
        '"path": "/nodes/0/children/0", "node_type": "section", '
        '"display_label": "1."' in scope
    )


def test_agent_usage_limit_becomes_a_pipeline_error(monkeypatch) -> None:
    model = TestModel()

    async def exceed_limit(*_: object, **kwargs: object) -> object:
        assert callable(kwargs["event_stream_handler"])
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


BOUNDARY_PAGE_TEXT = (
    "(c)  take no part in any consideration of the matter ; and\n"
    "(d)  be absent from the meeting during which the matter is discussed.\n"
    "18.  If a member of the Council discloses an interest under paragraph\n"
    "17, the disclosure shall be recorded in the minutes of the meeting.\n"
    "I, certify, in accordance with section 2 (1) of the Acts Authentication\n"
    "Act, Cap. A2, that this is a true copy of the Bill passed by both Houses.\n"
    "EXPLANATORY MEMORANDUM\n"
    "This Act provides a legal framework for the protection of personal\n"
    "information.\n"
)
BOUNDARY_PAGES = [
    {"pdf_page": 1, "text": "1. Opening\nComplete opening wording."},
    {"pdf_page": 2, "text": BOUNDARY_PAGE_TEXT},
]


def _boundary_plan(end_page: int, terminator: str | None = None) -> StructurePlan:
    payload: dict = {
        "legal_start_pdf_page": 1,
        "legal_end_pdf_page": end_page,
        "units": [
            {
                "unit_id": "body",
                "kind": "body",
                "start_pdf_page": 1,
                "end_pdf_page": end_page,
            }
        ],
    }
    if terminator is not None:
        payload["legal_end_terminator"] = terminator
    return StructurePlan.model_validate(payload)


def test_operative_text_ending_above_excluded_matter_has_no_page_granular_plan() -> None:
    with pytest.raises(PipelineError, match="outside the planned legal range"):
        structure_module._validate_plan(_boundary_plan(1), BOUNDARY_PAGES)

    with pytest.raises(PipelineError, match="excluded explanatory memorandum"):
        structure_module._validate_plan(_boundary_plan(2), BOUNDARY_PAGES)


def test_plan_accepts_excluded_matter_below_the_operative_terminator() -> None:
    structure_module._validate_plan(
        _boundary_plan(2, "I, certify, in accordance with"), BOUNDARY_PAGES
    )


def test_plan_rejects_an_operative_terminator_that_is_not_uniquely_locatable() -> None:
    with pytest.raises(PipelineError, match="operative terminator"):
        structure_module._validate_plan(
            _boundary_plan(2, "not present on the page"), BOUNDARY_PAGES
        )

    with pytest.raises(PipelineError, match="operative terminator"):
        structure_module._validate_plan(
            _boundary_plan(2, "the matter"), BOUNDARY_PAGES
        )


def test_plan_rejects_an_operative_terminator_above_remaining_legal_markers() -> None:
    with pytest.raises(PipelineError, match="outside the planned legal range"):
        structure_module._validate_plan(
            _boundary_plan(2, "(d)  be absent"), BOUNDARY_PAGES
        )


def test_plan_ignores_markers_in_the_bill_schedule_appendix() -> None:
    """A gazette closes with a bill-schedule table whose columns are not markers."""
    pages = [
        {"pdf_page": 1, "text": "1. Opening\nComplete opening wording."},
        {
            "pdf_page": 2,
            "text": (
                "(c)  the final paragraph of the Schedule.\n"
                "I certify, in accordance with section 2 (1) of the Acts\n"
                "Authentication Act, that this is a true copy.\n"
                "EXPLANATORY MEMORANDUM\n"
                "This Act establishes the Service.\n"
            ),
        },
        {
            "pdf_page": 3,
            "text": (
                "SCHEDULE TO THE NIGERIA REVENUE SERVICE (ESTABLISHMENT) BILL, 2025\n"
                "(1)\nShort Title\n(2)\nLong Title\n(3)\nSummary\n"
            ),
        },
    ]
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "legal_end_terminator": "I certify, in accordance with",
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 2,
                }
            ],
        }
    )

    structure_module._validate_plan(plan, pages)


def test_plan_ignores_word_parentheticals_in_a_split_bill_schedule_heading() -> None:
    """A rotated bill-schedule header breaks its heading and its title column."""
    pages = [
        {"pdf_page": 1, "text": "1. Opening\nComplete opening wording."},
        {
            "pdf_page": 2,
            "text": (
                "(c)  the final paragraph of the Schedule.\n"
                "I certify, in accordance with section 2 (1) of the Acts\n"
                "Authentication Act, that this is a true copy.\n"
            ),
        },
        {
            "pdf_page": 3,
            "text": (
                "Tertiary Educations \nTrust Fund\n"
                "(Establishment etc.) \nAct, the\n"
                "Customs, Excise \nTarrif\nfs, etc.\n"
                "(Consolidation) \nAct, the National\n"
                "Capital(Incentives) \nAct to amend\n"
                "SCHEDULE \nTO \nTHE NIGERIA\n T\nAX BILL, 2025\n"
            ),
        },
        {
            "pdf_page": 4,
            "text": (
                "Value Added Tax Act\n"
                "(Modification) Order 2021, to\n"
                "amend the Companies Income Tax\n"
                "BOLA AHMED TINUBU, GCFR\n"
            ),
        },
    ]
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "legal_end_terminator": "I certify, in accordance with",
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 2,
                }
            ],
        }
    )

    structure_module._validate_plan(plan, pages)


def test_plan_ignores_a_split_bill_schedule_heading_across_its_pages() -> None:
    """A rotated header breaks the heading while its columns stay marker-shaped."""
    pages = [
        {"pdf_page": 1, "text": "1. Opening\nComplete opening wording."},
        {
            "pdf_page": 2,
            "text": (
                "(c)  the final paragraph of the Schedule.\n"
                "I certify, in accordance with section 2 (1) of the Acts\n"
                "Authentication Act, that this is a true copy.\n"
            ),
        },
        {
            "pdf_page": 3,
            "text": (
                "SCHEDULE \nTO \nTHE NIGERIA\n T\nAX BILL, 2025\n"
                "(1)\nShort Title\nof the Bill\n(2)\nLong Title of the\nBill\n"
            ),
        },
        {
            "pdf_page": 4,
            "text": "(5)\nDate Passed by\nthe Senate\n28th May, 2025\n",
        },
    ]
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "legal_end_terminator": "I certify, in accordance with",
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 2,
                }
            ],
        }
    )

    structure_module._validate_plan(plan, pages)


def test_content_validation_follows_a_sentence_across_marginal_notes() -> None:
    """The Electoral Act prints section headings in the margin.

    pypdf extracts them inline, so a sentence running across a page break has
    the notes wedged between its halves. The wording is the real page 15/16
    seam of the Electoral Act 2026.
    """
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "19.",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": (
                                "appoint a period of seven days during which a "
                                "copy of the voters' register for each Local "
                                "Government, Area Council or Ward"
                            ),
                            "pdf_pages": [1, 2],
                        }
                    ],
                }
            ]
        }
    )
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "A  15         2026 No. 1 Electoral Act, 2026\n"
                "19. appoint a period\nof seven days during which a copy of the "
                "voters' register for each Local\n"
                "Power to\nprint or\nissue voters\ncard\nCustody of\nvoters'\nregister"
            ),
        },
        {
            "pdf_page": 2,
            "text": (
                "A  16         2026 No. 1 Electoral Act, 2026\n"
                "Revision\nofficer for\nhearing of\nclaims\nProprietary\n"
                "rights in the\nvoters' card\n"
                "Government, Area Council or Ward"
            ),
        },
    ]

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=2,
        pages=pages,
    )


def test_content_validation_still_rejects_halves_that_are_far_apart() -> None:
    """Continuing across a break is bounded; stitching distant text is not."""
    filler = " ".join(f"unrelated wording number {index}" for index in range(60))
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "Alpha wording that begins here beta ends there.",
                            "pdf_pages": [1, 2],
                        }
                    ],
                }
            ]
        }
    )
    pages = [
        {"pdf_page": 1, "text": f"1. Alpha wording that begins here\n{filler}"},
        {"pdf_page": 2, "text": f"{filler}\nbeta ends there."},
    ]

    with pytest.raises(PipelineError) as failure:
        structure_module._validate_draft(
            draft,
            allowed_types=structure_module.DOCUMENT_NODE_TYPES,
            page_count=2,
            pages=pages,
        )
    assert failure.value.code == "source_text_mismatch"


def test_one_unit_that_never_parses_does_not_discard_the_others(
    tmp_path: Path,
) -> None:
    """The Electoral Act lost twelve good units to one unparseable schedule."""
    cache_root = tmp_path / "source-cache"
    texts = ["1. Supremacy\nBinding wording.", "2. Other\nSecond wording."]
    extraction = cache_root / "extractions/extract.json"
    extraction.parent.mkdir(parents=True)
    extraction.write_text(
        json.dumps(
            {
                "stage": "extract",
                "status": "success",
                "extraction_version": 2,
                "source_id": SOURCE_ID,
                "page_count": 2,
                "pages": [
                    {"pdf_page": index, "text": text, "text_characters": len(text)}
                    for index, text in enumerate(texts, start=1)
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
                {"unit_id": "body", "kind": "body",
                 "start_pdf_page": 1, "end_pdf_page": 1},
                {"unit_id": "schedule-01", "kind": "schedule",
                 "display_label": "2.", "heading": "Other",
                 "start_pdf_page": 2, "end_pdf_page": 2},
            ],
        }
    )
    good = StructureDraft.model_validate(
        {"nodes": [{**_node("section", "1.", text="Binding wording."),
                    "heading": "Supremacy"}]}
    )
    progress: list[dict[str, object]] = []
    attempts = 0

    async def runner(
        raw_text: str,
        _: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal attempts
        if output_type is StructurePlan:
            validate(plan)
            return ModelRun(
                output=plan, model="model", output_mode="tool",
                latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        if output_type is StructureDraft and "schedule" in scope:
            attempts += 1
            raise PipelineError(
                "model_invalid_output",
                "model did not return valid structured output: Extra data",
                retryable=True,
            )
        if output_type is RepairPlan:
            nothing = RepairPlan.model_validate(
                {
                    "decisions": [
                        {
                            "unit_id": "schedule-01",
                            "action": "abort_unresolved",
                            "reason": "the model never returned parseable output",
                        }
                    ]
                }
            )
            validate(nothing)
            return ModelRun(
                output=nothing, model="model", output_mode="tool",
                latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        validate(good)
        return ModelRun(
            output=good, model="model", output_mode="tool", latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    # A whole schedule is still missing, so the run ends in failure. What
    # changed is that it is the critic's decision on a completed draft rather
    # than one unit's exception discarding the others.
    with pytest.raises(PipelineError) as failure:
        structure(
            extraction,
            execute=True,
            cache_root=cache_root,
            settings=StructureSettings("key", "https://example.com", "model", 30),
            primary_runner=runner,
            progress=progress.append,
        )
    assert failure.value.code == "structure_audit_failed"

    assert attempts == 2, "retried once, then carried rather than raised"
    events = [event["event"] for event in progress]
    assert "unit_failed" in events
    failed = next(e for e in progress if e["event"] == "unit_failed")
    assert failed["unit_id"] == "schedule-01"
    assert failed["error_code"] == "model_invalid_output"
    # The run reached the audit instead of dying at the unit.
    assert events.index("unit_failed") < events.index("units_completed")
    assert "audit_completed" in events
    assert list((cache_root / "structure-work").glob("*/001-body.json"))


def test_a_cross_reference_range_is_not_a_hidden_marker() -> None:
    """`section 62 (4) - (8)` cites a range; it does not open a subsection.

    The Electoral Act's part-04 was rejected for this. The structural form is
    `62.-(1)`, where the dash carries the marker; here the dash separates two
    citations.
    """
    text = (
        "knowingly and willfully contrary to the procedures prescribed under "
        "section 62 (4) – (8) of this Act."
    )
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "65.",
                    "pdf_page": 1,
                    "content_blocks": [
                        {"kind": "text", "text": text, "pdf_pages": [1]}
                    ],
                }
            ]
        }
    )

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module.DOCUMENT_NODE_TYPES,
        page_count=1,
        pages=[{"pdf_page": 1, "text": f"65. {text}"}],
    )


def test_a_dash_that_really_opens_an_enumeration_is_still_caught() -> None:
    text = 'a "competent authority" includes — (a) the Commission; or (b) the court.'
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "3.",
                    "pdf_page": 1,
                    "content_blocks": [
                        {"kind": "text", "text": text, "pdf_pages": [1]}
                    ],
                }
            ]
        }
    )

    with pytest.raises(PipelineError) as failure:
        structure_module._validate_draft(
            draft,
            allowed_types=structure_module.DOCUMENT_NODE_TYPES,
            page_count=1,
            pages=[{"pdf_page": 1, "text": f"3. {text}"}],
        )
    assert failure.value.code == "incomplete_structure_output"


def test_an_unreachable_critic_does_not_discard_a_completed_draft(
    tmp_path: Path,
) -> None:
    """Repair improves a draft that already exists; it is not a gate on it."""
    cache_root = tmp_path / "source-cache"
    extraction = _write_extraction(cache_root, "1. Supremacy\nBinding wording.")
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 1,
            "units": [
                {"unit_id": "body", "kind": "body",
                 "start_pdf_page": 1, "end_pdf_page": 1}
            ],
        }
    )
    partial = StructureDraft.model_validate(
        {"nodes": [{**_node("section", "1.", text="Binding"), "heading": "Supremacy"}]}
    )
    progress: list[dict[str, object]] = []
    critic_calls = 0

    async def runner(
        raw_text: str,
        _: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal critic_calls
        if output_type is StructurePlan:
            validate(plan)
            return ModelRun(
                output=plan, model="model", output_mode="tool", latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        if output_type is RepairPlan:
            critic_calls += 1
            raise PipelineError(
                "model_transient", "critic provider unreachable", retryable=True
            )
        validate(partial)
        return ModelRun(
            output=partial, model="model", output_mode="tool", latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    try:
        structure(
            extraction,
            execute=True,
            cache_root=cache_root,
            settings=StructureSettings("key", "https://example.com", "model", 30),
            primary_runner=runner,
            progress=progress.append,
        )
    except PipelineError as error:
        # Only the audit may end the run, never the unreachable critic.
        assert error.code == "structure_audit_failed"

    events = [event["event"] for event in progress]
    assert critic_calls == 2, "retried once before giving up on the critic"
    assert "critic_unavailable" in events
    unavailable = next(e for e in progress if e["event"] == "critic_unavailable")
    assert unavailable["error_code"] == "model_transient"
    # The draft was audited rather than thrown away with the critic.
    assert "audit_completed" in events


def test_an_unavailable_repair_patch_leaves_the_draft_it_could_not_improve(
    tmp_path: Path,
) -> None:
    """The third place a spent retry discarded a complete draft."""
    cache_root = tmp_path / "source-cache"
    extraction = _write_extraction(cache_root, "1. Supremacy\nBinding wording. Extra.")
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 1,
            "units": [
                {"unit_id": "body", "kind": "body",
                 "start_pdf_page": 1, "end_pdf_page": 1}
            ],
        }
    )
    partial = StructureDraft.model_validate(
        {"nodes": [{**_node("section", "1.", text="Binding"), "heading": "Supremacy"}]}
    )
    progress: list[dict[str, object]] = []
    patch_calls = 0

    async def runner(
        raw_text: str,
        _: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        nonlocal patch_calls
        if output_type is StructurePlan:
            validate(plan)
            return ModelRun(
                output=plan, model="model", output_mode="tool", latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        if output_type is RepairPlan:
            decision = RepairPlan.model_validate(
                {
                    "decisions": [
                        {"unit_id": "body", "action": "replace_unit",
                         "reason": "wording is missing from the draft"}
                    ]
                }
            )
            validate(decision)
            return ModelRun(
                output=decision, model="model", output_mode="tool",
                latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        if output_type is RepairPatch:
            patch_calls += 1
            raise PipelineError(
                "model_invalid_output", "patch never parsed", retryable=True
            )
        validate(partial)
        return ModelRun(
            output=partial, model="model", output_mode="tool", latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    try:
        structure(
            extraction,
            execute=True,
            cache_root=cache_root,
            settings=StructureSettings("key", "https://example.com", "model", 30),
            primary_runner=runner,
            progress=progress.append,
        )
    except PipelineError as error:
        assert error.code == "structure_audit_failed"

    events = [event["event"] for event in progress]
    assert patch_calls >= 2, "retried before giving up on the patch"
    discarded = [e for e in progress if e["event"] == "repair_patch_discarded"]
    assert discarded and discarded[0]["reason"] == "model_unavailable"
    # The run went on to audit the draft instead of dying inside repair.
    assert events.count("audit_completed") >= 2


def test_a_critic_that_keeps_returning_an_invalid_plan_is_not_fatal(
    tmp_path: Path,
) -> None:
    """The last place a spent retry could discard a finished draft."""
    cache_root = tmp_path / "source-cache"
    extraction = _write_extraction(cache_root, "1. Supremacy\nBinding wording. Extra.")
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 1,
            "units": [
                {"unit_id": "body", "kind": "body",
                 "start_pdf_page": 1, "end_pdf_page": 1}
            ],
        }
    )
    partial = StructureDraft.model_validate(
        {"nodes": [{**_node("section", "1.", text="Binding"), "heading": "Supremacy"}]}
    )
    progress: list[dict[str, object]] = []

    async def runner(
        raw_text: str,
        _: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        if output_type is StructurePlan:
            validate(plan)
            return ModelRun(
                output=plan, model="model", output_mode="tool", latency_seconds=0.1,
                usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
            )
        if output_type is RepairPlan:
            # Decides a unit that is not affected, so validation always refuses.
            wrong = RepairPlan.model_validate(
                {
                    "decisions": [
                        {"unit_id": "no-such-unit", "action": "replace_unit",
                         "reason": "the critic keeps naming the wrong unit"}
                    ]
                }
            )
            try:
                validate(wrong)
            except PipelineError as invalid:
                raise structure_module.RejectedModelOutput(
                    "model", wrong, invalid
                ) from invalid
            raise AssertionError("an invalid repair plan unexpectedly validated")
        validate(partial)
        return ModelRun(
            output=partial, model="model", output_mode="tool", latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    try:
        structure(
            extraction,
            execute=True,
            cache_root=cache_root,
            settings=StructureSettings("key", "https://example.com", "model", 30),
            primary_runner=runner,
            progress=progress.append,
        )
    except PipelineError as error:
        # Only the audit may end the run, never the critic.
        assert error.code == "structure_audit_failed"

    events = [event["event"] for event in progress]
    assert "critic_unavailable" in events
    assert "audit_completed" in events


def _write_two_page_extraction(cache_root: Path) -> Path:
    pages = ["1. Alpha\nFirst wording.", "2. Beta\nSecond wording."]
    extraction = cache_root / "extractions/extract.json"
    extraction.parent.mkdir(parents=True)
    extraction.write_text(
        json.dumps(
            {
                "stage": "extract",
                "status": "success",
                "extraction_version": 2,
                "source_id": SOURCE_ID,
                "page_count": 2,
                "pages": [
                    {
                        "pdf_page": index,
                        "text": text,
                        "text_characters": len(text),
                    }
                    for index, text in enumerate(pages, start=1)
                ],
                "result_path": "extractions/extract.json",
            }
        ),
        encoding="utf-8",
    )
    return extraction


def _page_draft(label: str, heading: str, text: str, pdf_page: int) -> StructureDraft:
    return StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": label,
                    "heading": heading,
                    "pdf_page": pdf_page,
                    "children": [],
                    "content_blocks": [
                        {"kind": "text", "text": text, "pdf_pages": [pdf_page]}
                    ],
                }
            ]
        }
    )


def test_unit_that_exhausts_its_attempts_is_split_instead_of_left_empty(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = _write_two_page_extraction(cache_root)
    plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                {
                    "unit_id": "body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 2,
                }
            ],
        }
    )
    split = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 2,
            "units": [
                {
                    "unit_id": "body-part-01",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                },
                {
                    "unit_id": "body-part-02",
                    "kind": "body",
                    "start_pdf_page": 2,
                    "end_pdf_page": 2,
                },
            ],
        }
    )
    drafts = {
        1: _page_draft("1.", "Alpha", "First wording.", 1),
        2: _page_draft("2.", "Beta", "Second wording.", 2),
    }
    progress: list[dict[str, object]] = []

    async def runner(
        raw_text: str,
        _: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        if output_type is StructurePlan:
            response: object = split if "split" in scope.casefold() else plan
        elif "PDF PAGE 1 ---" in raw_text and "PDF PAGE 2 ---" in raw_text:
            raise PipelineError(
                "model_transient", "codex exec exceeded 900s", retryable=True
            )
        else:
            response = drafts[1 if "PDF PAGE 1 ---" in raw_text else 2]
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
        settings=StructureSettings(
            "key", "https://example.com", "model", 30, max_repair_rounds=0
        ),
        primary_runner=runner,
        progress=progress.append,
    )

    events = [event["event"] for event in progress]
    assert "unit_split" in events
    assert events.index("unit_failed") < events.index("unit_split")
    assert "audit_completed" in events
    assert result["status"] == "success"
    audit = next(event for event in progress if event["event"] == "audit_completed")
    assert audit["claimed_characters"] == audit["source_characters"]
    assert result["summary"]["units"] == 2
    split_event = next(event for event in progress if event["event"] == "unit_split")
    assert split_event["parts"] == ["body-part-01", "body-part-02"]


def test_a_unit_that_cannot_be_split_is_not_sent_to_repair(tmp_path: Path) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = _write_extraction(cache_root, "1. Alpha\nFirst wording.")
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
    progress: list[dict[str, object]] = []

    async def runner(
        raw_text: str,
        _: StructureSettings,
        scope: str,
        output_type: type[object],
        validate: object,
    ) -> ModelRun:
        if output_type is not StructurePlan:
            raise PipelineError(
                "model_transient", "codex exec exceeded 900s", retryable=True
            )
        validate(plan)
        return ModelRun(
            output=plan,
            model="model",
            output_mode="tool",
            latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    with pytest.raises(PipelineError) as excinfo:
        structure(
            extraction,
            execute=True,
            cache_root=cache_root,
            settings=StructureSettings("key", "https://example.com", "model", 30),
            primary_runner=runner,
            progress=progress.append,
        )

    assert excinfo.value.code == "structure_audit_failed"
    events = [event["event"] for event in progress]
    assert "unit_split" not in events
    assert "critic_started" not in events
    assert "repairs_started" not in events


def test_a_schedule_part_unit_keeps_schedule_vocabulary() -> None:
    unit = StructureUnit.model_validate(
        {
            "unit_id": "schedule-01-part-02",
            "kind": "part",
            "display_label": "PART II",
            "heading": None,
            "start_pdf_page": 1,
            "end_pdf_page": 1,
        }
    )
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "part",
                    "display_label": "PART II",
                    "pdf_page": 1,
                    "children": [
                        {
                            "node_type": "section",
                            "display_label": "1.",
                            "pdf_page": 1,
                            "content_blocks": [
                                {
                                    "kind": "text",
                                    "text": "Exact wording.",
                                    "pdf_pages": [1],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    pages = [{"pdf_page": 1, "text": "PART II\n1. Exact wording."}]

    structure_module._validate_draft(
        draft,
        allowed_types=structure_module._unit_node_types(unit),
        page_count=1,
        pages=pages,
        target=unit,
    )

    assert draft.nodes[0].node_type == "schedule_part"
    assert draft.nodes[0].children[0].node_type == "schedule_paragraph"
