import json
from pathlib import Path

from pydantic_ai.models.test import TestModel

import openacts_pipeline.structure as structure_module
from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import StructureSettings
from openacts_pipeline.structure import ModelRun, structure
from openacts_pipeline.structure_schema import FocusPlan

SOURCE_ID = "sha256:" + "a" * 64


def test_structure_dry_run_does_not_call_the_model_or_write(tmp_path: Path) -> None:
    cache_root = tmp_path / "source-cache"
    extraction = cache_root / "extractions/extract.json"
    extraction.parent.mkdir(parents=True)
    extraction.write_text(
        json.dumps(
            {
                "stage": "extract",
                "status": "success",
                "extraction_version": 1,
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
