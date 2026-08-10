import asyncio
from pathlib import Path

import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import StructureSettings
from openacts_pipeline.structure_runtime import (
    ModelRun,
    RejectedCheckpoint,
    RejectedModelOutput,
    run_checkpointed,
)
from openacts_pipeline.structure_schema import StructurePlan


def test_checkpointed_agent_step_revalidates_without_recalling_model(
    tmp_path: Path,
) -> None:
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
    validations = 0

    def validate(output: object) -> None:
        nonlocal validations
        validations += 1
        assert output == plan

    async def runner(*_: object) -> ModelRun:
        return ModelRun(
            output=plan,
            model="model",
            output_mode="tool",
            latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    common = {
        "cache_root": tmp_path,
        "work_key": "work",
        "pass_index": 0,
        "pass_name": "plan",
        "source_id": "sha256:" + "a" * 64,
        "raw_text": "--- PDF PAGE 1 ---\n1. Example",
        "settings": StructureSettings("key", "https://example.com", "model", 30),
        "scope": "Plan the document.",
        "output_type": StructurePlan,
        "validate": validate,
        "structure_version": 6,
        "prompt_version": 20,
    }
    first, first_reused = asyncio.run(run_checkpointed(**common, runner=runner))

    async def forbidden(*_: object) -> ModelRun:
        raise AssertionError("checkpoint reuse called the model")

    resumed, resumed_reused = asyncio.run(run_checkpointed(**common, runner=forbidden))

    assert first.output == resumed.output == plan
    assert not first_reused
    assert resumed_reused
    assert validations == 2


def test_failed_checkpoint_is_revalidated_before_rejection(tmp_path: Path) -> None:
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

    async def rejected_runner(*_: object) -> ModelRun:
        raise RejectedModelOutput(
            "model",
            plan,
            PipelineError("invalid_structure_plan", "old validation failure"),
        )

    common = {
        "cache_root": tmp_path,
        "work_key": "work",
        "pass_index": 0,
        "pass_name": "plan",
        "source_id": "sha256:" + "a" * 64,
        "raw_text": "--- PDF PAGE 1 ---\n1. Example",
        "settings": StructureSettings("key", "https://example.com", "model", 30),
        "scope": "Plan the document.",
        "output_type": StructurePlan,
        "validate": lambda output: None,
        "structure_version": 6,
        "prompt_version": 20,
    }
    with pytest.raises(RejectedCheckpoint):
        asyncio.run(run_checkpointed(**common, runner=rejected_runner))

    async def forbidden(*_: object) -> ModelRun:
        raise AssertionError("failed checkpoint revalidation called the model")

    resumed, reused = asyncio.run(run_checkpointed(**common, runner=forbidden))

    assert resumed.output == plan
    assert resumed.output_mode == "revalidated_failure_checkpoint"
    assert resumed.usage == {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    assert reused


def test_stale_checkpoint_uses_immutable_scope_slot(tmp_path: Path) -> None:
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
    new_plan = StructurePlan.model_validate(
        {
            "legal_start_pdf_page": 1,
            "legal_end_pdf_page": 1,
            "units": [
                {
                    "unit_id": "new-body",
                    "kind": "body",
                    "start_pdf_page": 1,
                    "end_pdf_page": 1,
                }
            ],
        }
    )
    calls = 0

    async def runner(*_: object) -> ModelRun:
        nonlocal calls
        calls += 1
        return ModelRun(
            output=plan if calls == 1 else new_plan,
            model="model",
            output_mode="tool",
            latency_seconds=0.1,
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5},
        )

    common = {
        "cache_root": tmp_path,
        "work_key": "work",
        "pass_index": 1001,
        "source_id": "sha256:" + "a" * 64,
        "raw_text": "old audit",
        "settings": StructureSettings("key", "https://example.com", "model", 30),
        "scope": "old scope",
        "output_type": StructurePlan,
        "runner": runner,
        "validate": lambda output: None,
        "structure_version": 6,
        "prompt_version": 20,
    }
    asyncio.run(run_checkpointed(**common, pass_name="critic-1"))

    async def forbidden(*_: object) -> ModelRun:
        raise AssertionError("compatible stale checkpoint called the model")

    compatible, compatible_reused = asyncio.run(
        run_checkpointed(
            **{
                **common,
                "raw_text": "compatible audit",
                "scope": "compatible scope",
                "runner": forbidden,
            },
            pass_name="critic-1",
        )
    )
    assert compatible.output == plan
    assert compatible.output_mode == "revalidated_stale_checkpoint"
    assert compatible_reused

    def validate_new(output: object) -> None:
        if output != new_plan:
            raise PipelineError("invalid_structure_plan", "old plan")

    resumed = asyncio.run(
        run_checkpointed(
            **{
                **common,
                "raw_text": "new audit",
                "scope": "new scope",
                "validate": validate_new,
            },
            pass_name="critic-1",
        )
    )

    assert not resumed[1]
    assert calls == 2
    old = tmp_path / "structure-work/work/1001-critic-1.json"
    [scoped] = list(
        (tmp_path / "structure-work/work").glob("1001-critic-1-scope-*.json")
    )
    assert old.exists()
    assert scoped.exists()
