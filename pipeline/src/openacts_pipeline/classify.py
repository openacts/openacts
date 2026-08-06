"""Measure PDF text coverage before choosing an extraction route."""

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from openacts_pipeline.common import (
    PipelineError,
    iso_timestamp,
    utc_now,
    write_json_result,
)

ClassificationError = PipelineError

CLASSIFIER_VERSION = 1
MIN_SUBSTANTIVE_NON_WHITESPACE_CHARACTERS = 100
DOMINANT_IMAGE_COVERAGE = 0.8
MAX_PAGE_CONTENT_STREAM_BYTES = 16 * 1024 * 1024

TEXT_SHOW_OPERATORS = {b"Tj", b"TJ", b"'", b'"'}
VECTOR_PAINT_OPERATORS = {
    b"S",
    b"s",
    b"f",
    b"F",
    b"f*",
    b"B",
    b"B*",
    b"b",
    b"b*",
}


@dataclass(frozen=True)
class SourceInput:
    digest: str
    byte_length: int
    page_count: int
    receipt_path: str
    cache_path: Path

    @property
    def source_id(self) -> str:
        return f"sha256:{self.digest}"


def _positive_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ClassificationError("invalid_receipt", f"{field} must be positive")
    return value


def _load_source(receipt_path: Path, cache_root: Path) -> SourceInput:
    try:
        relative_receipt = receipt_path.resolve().relative_to(cache_root.resolve())
    except ValueError as exc:
        raise ClassificationError(
            "invalid_receipt", "receipt must be inside the cache root"
        ) from exc
    if not relative_receipt.parts or relative_receipt.parts[0] != "runs":
        raise ClassificationError(
            "invalid_receipt", "receipt must be an acquisition run"
        )

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClassificationError(
            "invalid_receipt", f"cannot read receipt: {exc}"
        ) from exc
    if not isinstance(receipt, dict) or receipt.get("status") != "success":
        raise ClassificationError(
            "invalid_receipt", "receipt must describe a successful acquisition"
        )
    source = receipt.get("source")
    if not isinstance(source, dict):
        raise ClassificationError("invalid_receipt", "receipt has no Source candidate")

    source_id = source.get("source_id")
    if not isinstance(source_id, str) or not source_id.startswith("sha256:"):
        raise ClassificationError("invalid_receipt", "source_id must use SHA-256")
    digest = source_id.removeprefix("sha256:")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ClassificationError("invalid_receipt", "source_id digest is invalid")

    cache_path = Path("sha256") / digest[:2] / f"{digest}.pdf"
    if receipt.get("cache_path") != cache_path.as_posix():
        raise ClassificationError(
            "invalid_receipt", "receipt cache_path does not match source_id"
        )
    return SourceInput(
        digest=digest,
        byte_length=_positive_integer(source.get("byte_length"), "byte_length"),
        page_count=_positive_integer(source.get("page_count"), "page_count"),
        receipt_path=relative_receipt.as_posix(),
        cache_path=cache_path,
    )


def _verify_cache(source: SourceInput, cache_root: Path) -> Path:
    cached_pdf = cache_root / source.cache_path
    try:
        byte_length = cached_pdf.stat().st_size
    except OSError as exc:
        raise ClassificationError(
            "cache_missing", f"cannot read cached PDF: {exc}"
        ) from exc
    if byte_length != source.byte_length:
        raise ClassificationError(
            "cache_size_mismatch", "cached PDF byte length changed after acquisition"
        )
    try:
        with cached_pdf.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise ClassificationError(
            "cache_unreadable", f"cannot hash cached PDF: {exc}"
        ) from exc
    if digest != source.digest:
        raise ClassificationError(
            "cache_digest_mismatch", "cached PDF digest changed after acquisition"
        )
    return cached_pdf


def _failed_page(
    pdf_page: int, error: str, *, content_stream_bytes: int | None = None
) -> dict[str, Any]:
    return {
        "pdf_page": pdf_page,
        "text_characters": None,
        "non_whitespace_characters": None,
        "substantive_text": False,
        "replacement_characters": None,
        "control_characters": None,
        "alphanumeric_ratio": None,
        "content_stream_bytes": content_stream_bytes,
        "text_show_operations": None,
        "invisible_text": None,
        "image_count": None,
        "dominant_image_coverage": None,
        "vector_operations": None,
        "unresolved_xobjects": None,
        "inspection_error": error,
    }


def _page_evidence(page: Any, pdf_page: int) -> tuple[dict[str, Any], str | None]:
    content_stream_bytes: int | None = None
    try:
        contents = page.get_contents()
        content_stream_bytes = len(contents.get_data()) if contents is not None else 0
        if content_stream_bytes > MAX_PAGE_CONTENT_STREAM_BYTES:
            return (
                _failed_page(
                    pdf_page,
                    "content_stream_too_large",
                    content_stream_bytes=content_stream_bytes,
                ),
                (
                    f"page {pdf_page}: decompressed content stream exceeds "
                    f"{MAX_PAGE_CONTENT_STREAM_BYTES} bytes"
                ),
            )

        resources = page.get("/Resources") or {}
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        xobjects = resources.get("/XObject", {})
        if hasattr(xobjects, "get_object"):
            xobjects = xobjects.get_object()

        render_mode = 0
        render_mode_stack: list[int] = []
        text_show_operations = 0
        invisible_text_operations = 0
        image_count = 0
        dominant_image_coverage = 0.0
        vector_operations = 0
        unresolved_xobjects = 0
        page_area = abs(float(page.cropbox.width) * float(page.cropbox.height))

        def visit_operand(
            operator: bytes,
            operands: list[Any],
            current_matrix: list[float],
            _text_matrix: list[float],
        ) -> None:
            nonlocal render_mode
            nonlocal text_show_operations
            nonlocal invisible_text_operations
            nonlocal image_count
            nonlocal dominant_image_coverage
            nonlocal vector_operations
            nonlocal unresolved_xobjects

            if operator == b"q":
                render_mode_stack.append(render_mode)
            elif operator == b"Q":
                render_mode = render_mode_stack.pop() if render_mode_stack else 0
            elif operator == b"Tr" and operands:
                render_mode = int(operands[0])
            elif operator in TEXT_SHOW_OPERATORS:
                text_show_operations += 1
                if render_mode == 3:
                    invisible_text_operations += 1
            elif operator in VECTOR_PAINT_OPERATORS:
                vector_operations += 1
            elif operator == b"Do" and operands:
                reference = xobjects.get(operands[0])
                if reference is None:
                    unresolved_xobjects += 1
                    return
                xobject = reference.get_object()
                if xobject.get("/Subtype") != "/Image":
                    return
                image_count += 1
                if page_area:
                    a, b, c, d = (float(value) for value in current_matrix[:4])
                    coverage = abs((a * d) - (b * c)) / page_area
                    dominant_image_coverage = max(
                        dominant_image_coverage, min(coverage, 1.0)
                    )

        text = page.extract_text(visitor_operand_before=visit_operand) or ""
    except Exception as exc:  # noqa: BLE001 - one malformed page must not hide others
        return (
            _failed_page(
                pdf_page,
                f"{type(exc).__name__}: {exc}",
                content_stream_bytes=content_stream_bytes,
            ),
            f"page {pdf_page}: embedded-text inspection failed: {exc}",
        )

    visible = [character for character in text if not character.isspace()]
    replacement_characters = text.count("\ufffd")
    control_characters = sum(
        unicodedata.category(character) in {"Cc", "Cs"} for character in visible
    )
    alphanumeric_ratio = (
        round(sum(character.isalnum() for character in visible) / len(visible), 6)
        if visible
        else None
    )
    return (
        {
            "pdf_page": pdf_page,
            "text_characters": len(text),
            "non_whitespace_characters": len(visible),
            "substantive_text": (
                len(visible) >= MIN_SUBSTANTIVE_NON_WHITESPACE_CHARACTERS
            ),
            "replacement_characters": replacement_characters,
            "control_characters": control_characters,
            "alphanumeric_ratio": alphanumeric_ratio,
            "content_stream_bytes": content_stream_bytes,
            "text_show_operations": text_show_operations,
            "invisible_text": invisible_text_operations > 0,
            "image_count": image_count,
            "dominant_image_coverage": round(dominant_image_coverage, 6),
            "vector_operations": vector_operations,
            "unresolved_xobjects": unresolved_xobjects,
            "inspection_error": None,
        },
        None,
    )


def _route_page(evidence: dict[str, Any]) -> dict[str, Any]:
    if evidence["inspection_error"] is not None:
        decision = ("unknown", "review", ["inspection_error"])
    elif evidence["replacement_characters"] or evidence["control_characters"]:
        decision = ("unknown", "review", ["corrupt_text"])
    elif evidence["dominant_image_coverage"] >= DOMINANT_IMAGE_COVERAGE:
        if evidence["invisible_text"]:
            decision = ("ocr", "review", ["dominant_raster", "invisible_text"])
        elif evidence["non_whitespace_characters"]:
            decision = (
                "unknown",
                "review",
                ["dominant_raster", "visible_text_over_raster"],
            )
        else:
            decision = (
                "scan_only",
                "ocr",
                ["dominant_raster", "no_extractable_text"],
            )
    elif evidence["invisible_text"]:
        decision = ("unknown", "review", ["invisible_text_without_raster"])
    elif evidence["substantive_text"]:
        decision = ("born_digital", "extract", ["substantive_visible_text"])
    elif evidence["non_whitespace_characters"]:
        decision = ("unknown", "review", ["sparse_text"])
    elif (
        evidence["image_count"] == 0
        and evidence["vector_operations"] == 0
        and evidence["unresolved_xobjects"] == 0
    ):
        decision = ("unknown", "skip", ["empty_page"])
    else:
        decision = ("unknown", "review", ["non_text_content"])

    text_layer, route, reason_codes = decision
    return {
        **evidence,
        "proposed_text_layer": text_layer,
        "proposed_route": route,
        "reason_codes": reason_codes,
    }


def _manual_review_pages(pages: list[dict[str, Any]]) -> list[int]:
    selected = {1, len(pages)}
    selected.update(
        page["pdf_page"] for page in pages if page["proposed_route"] == "review"
    )
    for route in ("extract", "ocr", "skip"):
        representative = next(
            (page["pdf_page"] for page in pages if page["proposed_route"] == route),
            None,
        )
        if representative is not None:
            selected.add(representative)
    return sorted(selected)


def _summarize(pages: list[dict[str, Any]]) -> dict[str, Any]:
    failed_pages = [page for page in pages if page["inspection_error"] is not None]
    pages_with_text = sum(
        page["inspection_error"] is None and page["non_whitespace_characters"] > 0
        for page in pages
    )
    coverage = round(pages_with_text / len(pages), 6)
    layers = {
        page["proposed_text_layer"]
        for page in pages
        if page["proposed_text_layer"] != "unknown"
    }
    has_digital = "born_digital" in layers
    has_raster = bool(layers & {"ocr", "scan_only"})
    if has_digital and has_raster:
        text_layer = "mixed"
    elif "ocr" in layers:
        text_layer = "ocr"
    elif "scan_only" in layers:
        text_layer = "scan_only"
    elif has_digital:
        text_layer = "born_digital"
    else:
        text_layer = "unknown"

    hard_review = any(
        page["proposed_route"] == "review" and page["reason_codes"] != ["sparse_text"]
        for page in pages
    )
    routes = {page["proposed_route"] for page in pages}
    if hard_review or not (routes & {"extract", "ocr"}):
        route = "manual_review"
    elif "extract" in routes and "ocr" in routes:
        route = "hybrid"
    elif "extract" in routes:
        route = "extract"
    else:
        route = "ocr"

    route_counts = {
        candidate: sum(page["proposed_route"] == candidate for page in pages)
        for candidate in ("extract", "ocr", "skip", "review")
    }
    return {
        "pages_with_embedded_text": pages_with_text,
        "pages_without_embedded_text": len(pages) - pages_with_text - len(failed_pages),
        "pages_with_inspection_errors": len(failed_pages),
        "embedded_text_coverage": coverage,
        "page_route_counts": route_counts,
        "proposed_text_layer": text_layer,
        "proposed_route": route,
        "manual_review_pages": _manual_review_pages(pages),
        "human_review_required": True,
    }


def classify(receipt_path: Path, *, cache_root: Path) -> dict[str, Any]:
    started_at = utc_now()
    source = _load_source(receipt_path, cache_root)
    cached_pdf = _verify_cache(source, cache_root)
    try:
        reader = PdfReader(cached_pdf, strict=False)
        if reader.is_encrypted:
            raise ClassificationError("pdf_encrypted", "encrypted PDFs are unsupported")
        if len(reader.pages) != source.page_count:
            raise ClassificationError(
                "cache_page_count_mismatch",
                "cached PDF page count changed after acquisition",
            )
    except ClassificationError:
        raise
    except Exception as exc:
        raise ClassificationError(
            "pdf_unreadable", f"cannot inspect cached PDF: {exc}"
        ) from exc

    pages: list[dict[str, Any]] = []
    warnings: list[str] = []
    for pdf_page, page in enumerate(reader.pages, start=1):
        evidence, warning = _page_evidence(page, pdf_page)
        pages.append(_route_page(evidence))
        if warning:
            warnings.append(warning)
    summary = _summarize(pages)
    warnings.append(
        "classification is a routing proposal; confirm sampled pages before "
        "accepting the proposed text layer"
    )

    report = {
        "stage": "classify",
        "status": "success",
        "classifier_version": CLASSIFIER_VERSION,
        "thresholds": {
            "minimum_substantive_non_whitespace_characters": (
                MIN_SUBSTANTIVE_NON_WHITESPACE_CHARACTERS
            ),
            "dominant_image_coverage": DOMINANT_IMAGE_COVERAGE,
            "maximum_page_content_stream_bytes": MAX_PAGE_CONTENT_STREAM_BYTES,
        },
        "started_at": iso_timestamp(started_at),
        "finished_at": iso_timestamp(utc_now()),
        "network_access": False,
        "input_receipt": source.receipt_path,
        "source_id": source.source_id,
        "cache_path": source.cache_path.as_posix(),
        "byte_length": source.byte_length,
        "page_count": source.page_count,
        "pages": pages,
        "summary": summary,
        "warnings": warnings,
    }
    run_id = f"{started_at:%Y%m%dT%H%M%SZ}-{source.digest[:8]}-{uuid.uuid4().hex[:8]}"
    write_json_result(cache_root, report, Path("classifications") / f"{run_id}.json")
    return report
