"""Extract native PDF text into a page-preserving processing artifact."""

import json
import uuid
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf import __version__ as pypdf_version

from openacts_pipeline.common import (
    PipelineError,
    iso_timestamp,
    utc_now,
    verify_cached_pdf,
    write_json_result,
)

ExtractionError = PipelineError

EXTRACTION_VERSION = 1
SUPPORTED_CLASSIFIER_VERSION = 1


def _positive_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ExtractionError("invalid_classification", f"{field} must be positive")
    return value


def _load_classification(
    classification_path: Path, cache_root: Path
) -> tuple[dict[str, Any], str, Path, Path]:
    try:
        relative_path = classification_path.resolve().relative_to(cache_root.resolve())
    except ValueError as exc:
        raise ExtractionError(
            "invalid_classification", "classification must be inside the cache root"
        ) from exc
    if not relative_path.parts or relative_path.parts[0] != "classifications":
        raise ExtractionError(
            "invalid_classification", "input must be a classification report"
        )

    try:
        report = json.loads(classification_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractionError(
            "invalid_classification", f"cannot read classification: {exc}"
        ) from exc
    if (
        not isinstance(report, dict)
        or report.get("stage") != "classify"
        or report.get("status") != "success"
    ):
        raise ExtractionError(
            "invalid_classification", "input must be a successful classification"
        )
    if report.get("result_path") != relative_path.as_posix():
        raise ExtractionError(
            "invalid_classification", "classification result_path does not match input"
        )
    if report.get("classifier_version") != SUPPORTED_CLASSIFIER_VERSION:
        raise ExtractionError(
            "unsupported_classifier_version",
            "classification version is unsupported",
        )

    source_id = report.get("source_id")
    if not isinstance(source_id, str) or not source_id.startswith("sha256:"):
        raise ExtractionError("invalid_classification", "source_id must use SHA-256")
    digest = source_id.removeprefix("sha256:")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ExtractionError("invalid_classification", "source_id digest is invalid")

    byte_length = _positive_integer(report.get("byte_length"), "byte_length")
    page_count = _positive_integer(report.get("page_count"), "page_count")
    cache_path = Path("sha256") / digest[:2] / f"{digest}.pdf"
    if report.get("cache_path") != cache_path.as_posix():
        raise ExtractionError(
            "invalid_classification", "cache_path does not match source_id"
        )

    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ExtractionError("invalid_classification", "classification has no summary")
    route = summary.get("proposed_route")
    if route != "extract":
        raise ExtractionError(
            "unsupported_classification_route",
            f"native extraction does not support route: {route}",
        )

    pages = report.get("pages")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise ExtractionError(
            "invalid_classification", "classification page count is inconsistent"
        )
    for pdf_page, page in enumerate(pages, start=1):
        if (
            not isinstance(page, dict)
            or page.get("pdf_page") != pdf_page
            or page.get("inspection_error") is not None
        ):
            raise ExtractionError(
                "invalid_classification", "classification page evidence is inconsistent"
            )

    report["byte_length"] = byte_length
    report["page_count"] = page_count
    return report, digest, cache_path, relative_path


def extract(classification_path: Path, *, cache_root: Path) -> dict[str, Any]:
    started_at = utc_now()
    classification, digest, cache_path, relative_input = _load_classification(
        classification_path, cache_root
    )
    cached_pdf = verify_cached_pdf(
        cache_root,
        cache_path,
        expected_byte_length=classification["byte_length"],
        expected_digest=digest,
    )
    try:
        reader = PdfReader(cached_pdf, strict=False)
        if reader.is_encrypted:
            raise ExtractionError("pdf_encrypted", "encrypted PDFs are unsupported")
        if len(reader.pages) != classification["page_count"]:
            raise ExtractionError(
                "cache_page_count_mismatch",
                "cached PDF page count changed after classification",
            )
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            "pdf_unreadable", f"cannot inspect cached PDF: {exc}"
        ) from exc

    pages: list[dict[str, Any]] = []
    for pdf_page, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise ExtractionError(
                "page_extraction_failed", f"page {pdf_page}: {exc}"
            ) from exc
        pages.append(
            {
                "pdf_page": pdf_page,
                "text": text,
                "text_characters": len(text),
            }
        )

    summary = {
        "pages_extracted": len(pages),
        "empty_pages": sum(not page["text"].strip() for page in pages),
        "text_characters": sum(page["text_characters"] for page in pages),
    }
    artifact = {
        "stage": "extract",
        "status": "success",
        "extraction_version": EXTRACTION_VERSION,
        "started_at": iso_timestamp(started_at),
        "finished_at": iso_timestamp(utc_now()),
        "network_access": False,
        "input_classification": relative_input.as_posix(),
        "source_id": classification["source_id"],
        "cache_path": cache_path.as_posix(),
        "byte_length": classification["byte_length"],
        "page_count": classification["page_count"],
        "extractor": {"name": "pypdf", "version": pypdf_version},
        "pages": pages,
        "summary": summary,
    }
    run_id = f"{started_at:%Y%m%dT%H%M%SZ}-{digest[:8]}-{uuid.uuid4().hex[:8]}"
    write_json_result(cache_root, artifact, Path("extractions") / f"{run_id}.json")
    return {
        "stage": artifact["stage"],
        "status": artifact["status"],
        "source_id": artifact["source_id"],
        "page_count": artifact["page_count"],
        "summary": summary,
        "result_path": artifact["result_path"],
    }
