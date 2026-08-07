from __future__ import annotations

from pathlib import Path

import pytest

from openacts_pipeline.common import PipelineError, write_json_result
from openacts_pipeline.ocr import (
    DETECTION_MODEL,
    MODEL_FILES,
    RECOGNITION_MODEL,
    _checkpoint_directory,
    _checkpoint_from_result,
    _profile,
    extract_ocr_pages,
    setup_ocr_models,
)


def test_checkpoint_preserves_text_scores_and_boxes() -> None:
    result = {
        "res": {
            "rec_texts": [" Constitution ", "Federal Republic"],
            "rec_scores": [0.98, 0.91],
            "rec_boxes": [[1, 2, 30, 12], [1, 20, 80, 30]],
        }
    }

    checkpoint = _checkpoint_from_result(
        result, source_id=f"sha256:{'a' * 64}", pdf_page=1
    )

    assert checkpoint["text"] == "Constitution\nFederal Republic"
    assert checkpoint["text_characters"] == len(checkpoint["text"])
    assert checkpoint["lines"][0] == {
        "text": " Constitution ",
        "score": 0.98,
        "box": [1, 2, 30, 12],
    }


def test_extract_ocr_pages_reuses_complete_checkpoint(tmp_path: Path) -> None:
    cache_root = tmp_path / "source-cache"
    model_root = cache_root / "models" / "paddleocr" / "official_models"
    for model_name in (DETECTION_MODEL, RECOGNITION_MODEL):
        model_path = model_root / model_name
        model_path.mkdir(parents=True)
        for filename in MODEL_FILES:
            (model_path / filename).touch()
    source_id = f"sha256:{'b' * 64}"
    relative_path = _checkpoint_directory(cache_root, source_id) / "page-0001.json"
    checkpoint = {
        "stage": "ocr_page",
        "status": "success",
        "source_id": source_id,
        "pdf_page": 1,
        "ocr_profile": _profile(),
        "text": "cached text",
        "text_characters": 11,
        "lines": [],
    }
    write_json_result(cache_root, checkpoint, relative_path)

    result = extract_ocr_pages(tmp_path / "unused.pdf", [1], cache_root, source_id)

    assert result["pages"][1]["text"] == "cached text"
    assert result["checkpoints_reused"] == 1


def test_ocr_setup_preview_does_not_create_model_cache(tmp_path: Path) -> None:
    cache_root = tmp_path / "source-cache"

    result = setup_ocr_models(cache_root=cache_root, execute=False)

    assert result["status"] == "needs_setup"
    assert result["network_access"] is False
    assert not cache_root.exists()


def test_extract_ocr_pages_requires_explicit_model_setup(tmp_path: Path) -> None:
    with pytest.raises(PipelineError) as caught:
        extract_ocr_pages(
            tmp_path / "unused.pdf",
            [1],
            tmp_path / "source-cache",
            f"sha256:{'c' * 64}",
        )

    assert caught.value.code == "ocr_models_missing"
