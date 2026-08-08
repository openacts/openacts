import asyncio
from pathlib import Path

from openacts_pipeline.config import StructureSettings
from openacts_pipeline.structure_runtime import ModelRun, run_checkpointed
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
