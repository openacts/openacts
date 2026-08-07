"""Run bounded, offline PaddleOCR for pages selected by classification."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from openacts_pipeline.common import PipelineError, write_json_result

OCR_VERSION = 1
OCR_DPI = 300
OCR_BATCH_SIZE = 2
OCR_CPU_THREADS = 4
DETECTION_MODEL = "PP-OCRv6_medium_det"
RECOGNITION_MODEL = "PP-OCRv6_medium_rec"
MODEL_FILES = ("inference.json", "inference.pdiparams", "inference.yml")


def _model_cache(cache_root: Path) -> Path:
    return (cache_root / "models" / "paddleocr").resolve()


def _model_paths(cache_root: Path) -> dict[str, Path]:
    root = _model_cache(cache_root) / "official_models"
    return {
        DETECTION_MODEL: root / DETECTION_MODEL,
        RECOGNITION_MODEL: root / RECOGNITION_MODEL,
    }


def _model_installed(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in MODEL_FILES)


def _profile() -> dict[str, Any]:
    return {
        "ocr_version": OCR_VERSION,
        "engine": "paddleocr",
        "engine_version": "3.7.0",
        "runtime": "paddlepaddle",
        "runtime_version": "3.2.0",
        "detection_model": DETECTION_MODEL,
        "recognition_model": RECOGNITION_MODEL,
        "dpi": OCR_DPI,
        "batch_size": OCR_BATCH_SIZE,
        "device": "cpu",
        "cpu_threads": OCR_CPU_THREADS,
    }


def _configure_paddle_cache(cache_root: Path) -> None:
    cache = _model_cache(cache_root)
    loaded_cache = sys.modules.get("paddlex.utils.cache")
    if loaded_cache is not None and Path(loaded_cache.CACHE_DIR) != cache:
        raise PipelineError(
            "ocr_cache_already_configured",
            "PaddleX was imported before the repository model cache was configured",
        )
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache)
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "huggingface")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def setup_ocr_models(*, cache_root: Path, execute: bool) -> dict[str, Any]:
    """Report or explicitly download the two pinned OCR models."""
    paths = _model_paths(cache_root)
    missing = [name for name, path in paths.items() if not _model_installed(path)]
    downloaded = bool(execute and missing)
    if execute and missing:
        _configure_paddle_cache(cache_root)
        try:
            from paddlex.inference.utils.official_models import official_models

            for model_name in missing:
                official_models[model_name]
        except ModuleNotFoundError as exc:
            raise PipelineError(
                "ocr_dependencies_missing",
                "install the OCR dependency group with: uv sync --project pipeline "
                "--group ocr",
            ) from exc
        except Exception as exc:
            raise PipelineError(
                "ocr_model_setup_failed",
                f"cannot download PaddleOCR models: {exc}",
                retryable=True,
            ) from exc
        missing = [name for name, path in paths.items() if not _model_installed(path)]

    if execute and missing:
        raise PipelineError(
            "ocr_model_setup_failed",
            f"model download completed without: {', '.join(missing)}",
            retryable=True,
        )
    return {
        "stage": "ocr_setup",
        "status": "ready" if not missing else "needs_setup",
        "execute": execute,
        "network_access": downloaded,
        "model_cache": _model_cache(cache_root).relative_to(cache_root).as_posix(),
        "models": [
            {
                "name": name,
                "path": path.relative_to(cache_root).as_posix(),
                "installed": _model_installed(path),
            }
            for name, path in paths.items()
        ],
    }


def _require_models(cache_root: Path) -> dict[str, Path]:
    paths = _model_paths(cache_root)
    missing = [name for name, path in paths.items() if not _model_installed(path)]
    if missing:
        raise PipelineError(
            "ocr_models_missing",
            "run make ocr-setup-execute before extracting OCR pages",
        )
    return paths


def _checkpoint_directory(cache_root: Path, source_id: str) -> Path:
    key_input = json.dumps(
        {"source_id": source_id, "profile": _profile()}, sort_keys=True
    ).encode()
    work_key = hashlib.sha256(key_input).hexdigest()[:20]
    return Path("extraction-work") / work_key


def _load_checkpoint(
    cache_root: Path, relative_path: Path, source_id: str, pdf_page: int
) -> dict[str, Any] | None:
    path = cache_root / relative_path
    if not path.exists():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(
            "invalid_ocr_checkpoint", f"cannot read page {pdf_page} checkpoint: {exc}"
        ) from exc
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get("stage") != "ocr_page"
        or checkpoint.get("status") != "success"
        or checkpoint.get("source_id") != source_id
        or checkpoint.get("pdf_page") != pdf_page
        or checkpoint.get("ocr_profile") != _profile()
        or checkpoint.get("result_path") != relative_path.as_posix()
        or not isinstance(checkpoint.get("text"), str)
        or checkpoint.get("text_characters") != len(checkpoint["text"])
    ):
        raise PipelineError(
            "invalid_ocr_checkpoint", f"page {pdf_page} checkpoint is inconsistent"
        )
    return checkpoint


def _json_list(value: Any) -> list[Any]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    return value if isinstance(value, list) else []


def _checkpoint_from_result(
    result: Any,
    *,
    source_id: str,
    pdf_page: int,
) -> dict[str, Any]:
    payload = result.json if hasattr(result, "json") else result
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        raise PipelineError(
            "ocr_invalid_output", f"page {pdf_page}: OCR result is not an object"
        )
    if isinstance(payload.get("res"), dict):
        payload = payload["res"]
    texts = payload.get("rec_texts")
    if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
        raise PipelineError(
            "ocr_invalid_output", f"page {pdf_page}: OCR result has no text list"
        )
    scores = _json_list(payload.get("rec_scores"))
    boxes = _json_list(payload.get("rec_boxes"))
    text = "\n".join(line.strip() for line in texts if line.strip())
    lines = [
        {
            "text": line,
            "score": float(scores[index]) if index < len(scores) else None,
            "box": boxes[index] if index < len(boxes) else None,
        }
        for index, line in enumerate(texts)
        if line.strip()
    ]
    return {
        "stage": "ocr_page",
        "status": "success",
        "source_id": source_id,
        "pdf_page": pdf_page,
        "ocr_profile": _profile(),
        "text": text,
        "text_characters": len(text),
        "lines": lines,
    }


def _render_pages(pdf_path: Path, page_numbers: list[int]) -> list[Any]:
    try:
        import pypdfium2
    except ModuleNotFoundError as exc:
        raise PipelineError(
            "ocr_dependencies_missing",
            "install the OCR dependency group with: uv sync --project pipeline "
            "--group ocr",
        ) from exc

    images: list[Any] = []
    try:
        document = pypdfium2.PdfDocument(pdf_path)
        try:
            for page_number in page_numbers:
                page = document[page_number - 1]
                try:
                    bitmap = page.render(scale=OCR_DPI / 72)
                    try:
                        # PDFium exposes a view over its bitmap buffer; copy before
                        # closing the bitmap so PaddleOCR receives owned BGR bytes.
                        images.append(bitmap.to_numpy().copy())
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            document.close()
    except Exception as exc:
        raise PipelineError(
            "ocr_render_failed", f"cannot render OCR pages: {exc}"
        ) from exc
    return images


def extract_ocr_pages(
    pdf_path: Path,
    page_numbers: list[int],
    cache_root: Path,
    source_id: str,
) -> dict[str, Any]:
    """OCR selected PDF pages, reusing complete page checkpoints."""
    paths = _require_models(cache_root)
    work_directory = _checkpoint_directory(cache_root, source_id)
    pages: dict[int, dict[str, Any]] = {}
    pending: list[int] = []
    reused = 0
    for pdf_page in page_numbers:
        relative_path = work_directory / f"page-{pdf_page:04d}.json"
        checkpoint = _load_checkpoint(cache_root, relative_path, source_id, pdf_page)
        if checkpoint is None:
            pending.append(pdf_page)
        else:
            pages[pdf_page] = checkpoint
            reused += 1

    if pending:
        _configure_paddle_cache(cache_root)
        try:
            from paddleocr import PaddleOCR

            engine = PaddleOCR(
                text_detection_model_name=DETECTION_MODEL,
                text_detection_model_dir=str(paths[DETECTION_MODEL]),
                text_recognition_model_name=RECOGNITION_MODEL,
                text_recognition_model_dir=str(paths[RECOGNITION_MODEL]),
                text_recognition_batch_size=OCR_BATCH_SIZE,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
                cpu_threads=OCR_CPU_THREADS,
            )
            for offset in range(0, len(pending), OCR_BATCH_SIZE):
                batch_pages = pending[offset : offset + OCR_BATCH_SIZE]
                images = _render_pages(pdf_path, batch_pages)
                results = list(engine.predict(images))
                if len(results) != len(batch_pages):
                    raise PipelineError(
                        "ocr_invalid_output",
                        "PaddleOCR returned a different number of results than inputs",
                    )
                for pdf_page, result in zip(batch_pages, results, strict=True):
                    checkpoint = _checkpoint_from_result(
                        result, source_id=source_id, pdf_page=pdf_page
                    )
                    relative_path = work_directory / f"page-{pdf_page:04d}.json"
                    write_json_result(cache_root, checkpoint, relative_path)
                    pages[pdf_page] = checkpoint
        except PipelineError:
            raise
        except ModuleNotFoundError as exc:
            raise PipelineError(
                "ocr_dependencies_missing",
                "install the OCR dependency group with: uv sync --project pipeline "
                "--group ocr",
            ) from exc
        except Exception as exc:
            raise PipelineError(
                "ocr_failed", f"PaddleOCR failed: {exc}", retryable=True
            ) from exc

    return {
        "pages": pages,
        "metadata": _profile(),
        "checkpoints_reused": reused,
    }
