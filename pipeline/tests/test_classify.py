from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

import openacts_pipeline.classify as classify_module
from openacts_pipeline.classify import ClassificationError, classify

SUBSTANTIVE_TEXT = (
    "The Nigeria Data Protection Act establishes rights duties safeguards and "
    "lawful processing requirements for personal data throughout the federation."
)


def _pdf_bytes(*page_kinds: str) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    image = DecodedStreamObject()
    image.set_data(b"\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image_reference = writer._add_object(image)

    for kind in page_kinds:
        page = writer.add_blank_page(width=612, height=792)
        resources = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        if kind in {"scan", "ocr", "decorative"}:
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Im1"): image_reference}
            )
        page[NameObject("/Resources")] = resources

        commands: list[str] = []
        if kind in {"digital", "decorative"}:
            commands.append(f"BT /F1 12 Tf 72 720 Td 0 Tr ({SUBSTANTIVE_TEXT}) Tj ET")
        elif kind == "sparse":
            commands.append("BT /F1 10 Tf 72 760 Td 0 Tr (NDPA 2023) Tj ET")
        elif kind == "ocr":
            commands.append(f"BT /F1 12 Tf 72 720 Td 3 Tr ({SUBSTANTIVE_TEXT}) Tj ET")
        if kind in {"scan", "ocr"}:
            commands.insert(0, "q 612 0 0 792 0 0 cm /Im1 Do Q")
        elif kind == "decorative":
            commands.insert(0, "q 48 0 0 48 500 720 cm /Im1 Do Q")

        contents = DecodedStreamObject()
        contents.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(contents)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def acquired_source(
    tmp_path: Path, *, payload: bytes, page_count: int
) -> tuple[Path, Path, Path]:
    cache_root = tmp_path / "source-cache"
    digest = hashlib.sha256(payload).hexdigest()
    cache_relative = Path("sha256") / digest[:2] / f"{digest}.pdf"
    cached_pdf = cache_root / cache_relative
    cached_pdf.parent.mkdir(parents=True)
    cached_pdf.write_bytes(payload)

    receipt = {
        "status": "success",
        "cache_path": cache_relative.as_posix(),
        "source": {
            "source_id": f"sha256:{digest}",
            "byte_length": len(payload),
            "page_count": page_count,
        },
    }
    receipt_path = cache_root / "runs" / "acquire.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path, cache_root, cached_pdf


def test_classify_uses_real_pdf_evidence_without_saving_text(
    tmp_path: Path,
) -> None:
    payload = _pdf_bytes("digital", "blank", "scan", "ocr", "decorative")
    receipt_path, cache_root, _ = acquired_source(
        tmp_path, payload=payload, page_count=5
    )
    corpus_path = tmp_path / "corpus" / "sources.jsonl"
    corpus_path.parent.mkdir()
    corpus_path.write_text("unchanged\n", encoding="utf-8")

    result = classify(receipt_path, cache_root=cache_root)

    assert result["status"] == "success"
    assert result["summary"]["proposed_text_layer"] == "mixed"
    assert result["summary"]["proposed_route"] == "manual_review"
    assert [page["proposed_route"] for page in result["pages"]] == [
        "extract",
        "skip",
        "ocr",
        "review",
        "extract",
    ]
    assert [page["proposed_text_layer"] for page in result["pages"]] == [
        "born_digital",
        "unknown",
        "scan_only",
        "ocr",
        "born_digital",
    ]
    assert result["pages"][2]["dominant_image_coverage"] == 1.0
    assert result["pages"][4]["dominant_image_coverage"] < 0.01
    assert result["pages"][3]["invisible_text"] is True
    assert result["summary"]["human_review_required"] is True
    assert corpus_path.read_text(encoding="utf-8") == "unchanged\n"
    report_path = cache_root / result["result_path"]
    report_text = report_path.read_text(encoding="utf-8")
    assert json.loads(report_text) == result
    assert SUBSTANTIVE_TEXT not in report_text


def test_sparse_header_does_not_turn_digital_document_into_mixed(
    tmp_path: Path,
) -> None:
    payload = _pdf_bytes("digital", "sparse", "digital")
    receipt_path, cache_root, _ = acquired_source(
        tmp_path, payload=payload, page_count=3
    )

    result = classify(receipt_path, cache_root=cache_root)

    assert result["summary"]["proposed_text_layer"] == "born_digital"
    assert result["summary"]["proposed_route"] == "extract"
    assert result["pages"][1]["proposed_route"] == "review"
    assert "sparse_text" in result["pages"][1]["reason_codes"]
    assert 2 in result["summary"]["manual_review_pages"]


def test_large_page_content_stream_routes_to_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _pdf_bytes("digital")
    receipt_path, cache_root, _ = acquired_source(
        tmp_path, payload=payload, page_count=1
    )
    monkeypatch.setattr(classify_module, "MAX_PAGE_CONTENT_STREAM_BYTES", 1)

    result = classify(receipt_path, cache_root=cache_root)

    assert result["pages"][0]["proposed_route"] == "review"
    assert "inspection_error" in result["pages"][0]["reason_codes"]
    assert result["summary"]["proposed_route"] == "manual_review"


def test_classify_rejects_changed_cached_bytes(tmp_path: Path) -> None:
    payload = _pdf_bytes("digital")
    receipt_path, cache_root, cached_pdf = acquired_source(
        tmp_path, payload=payload, page_count=1
    )
    cached_pdf.write_bytes(b"changed")

    with pytest.raises(ClassificationError) as caught:
        classify(receipt_path, cache_root=cache_root)

    assert caught.value.code == "cache_size_mismatch"
    assert not (cache_root / "classifications").exists()
