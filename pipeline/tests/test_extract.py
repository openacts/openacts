from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import openacts_pipeline.extract as extract_module
from openacts_pipeline.classify import classify
from openacts_pipeline.extract import ExtractionError, extract

SUBSTANTIVE_TEXT = (
    "The Nigeria Data Protection Act establishes rights duties safeguards and "
    "lawful processing requirements for personal data throughout the federation."
)


def _pdf_bytes(*texts: str) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        contents = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        command = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET" if text else ""
        contents.set_data(command.encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(contents)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _classification(
    tmp_path: Path,
) -> tuple[Path, Path, Path, bytes, dict[str, object]]:
    payload = _pdf_bytes(
        SUBSTANTIVE_TEXT,
        "A 718 2023 No. 37 Nigeria Data Protection Act 2023",
        "",
        SUBSTANTIVE_TEXT,
    )
    digest = hashlib.sha256(payload).hexdigest()
    cache_root = tmp_path / "source-cache"
    cache_path = Path("sha256") / digest[:2] / f"{digest}.pdf"
    cached_pdf = cache_root / cache_path
    cached_pdf.parent.mkdir(parents=True)
    cached_pdf.write_bytes(payload)

    receipt = {
        "status": "success",
        "cache_path": cache_path.as_posix(),
        "source": {
            "source_id": f"sha256:{digest}",
            "byte_length": len(payload),
            "page_count": 4,
        },
    }
    receipt_path = cache_root / "runs" / "acquire.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    classification = classify(receipt_path, cache_root=cache_root)
    classification_path = cache_root / classification["result_path"]
    return classification_path, cache_root, cached_pdf, payload, classification


def test_extract_preserves_every_page_without_touching_corpus(
    tmp_path: Path,
) -> None:
    classification_path, cache_root, _, payload, classification = _classification(
        tmp_path
    )
    corpus_path = tmp_path / "corpus" / "provisions.jsonl"
    corpus_path.parent.mkdir()
    corpus_path.write_text("unchanged\n", encoding="utf-8")

    result = extract(classification_path, cache_root=cache_root)

    assert result["status"] == "success"
    assert "pages" not in result
    assert result["summary"]["pages_extracted"] == 3
    assert result["summary"]["pages_output"] == 4
    assert result["summary"]["native_pages"] == 3
    assert result["summary"]["ocr_pages"] == 0
    assert result["summary"]["skipped_pages"] == 1
    assert result["summary"]["empty_pages"] == 1
    assert result["summary"]["text_characters"] > 0
    assert corpus_path.read_text(encoding="utf-8") == "unchanged\n"

    artifact_path = cache_root / result["result_path"]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    expected_texts = [
        page.extract_text() or "" for page in PdfReader(BytesIO(payload)).pages
    ]
    assert artifact["input_classification"] == classification["result_path"]
    assert artifact["extractor"]["name"] == "pypdf"
    assert artifact["extractor"]["version"]
    assert artifact["ocr_extractor"] is None
    assert [page["pdf_page"] for page in artifact["pages"]] == [1, 2, 3, 4]
    assert [page["text"] for page in artifact["pages"]] == [
        expected_texts[0],
        expected_texts[1],
        "",
        expected_texts[3],
    ]
    assert [page["method"] for page in artifact["pages"]] == [
        "native",
        "native",
        "skip",
        "native",
    ]
    assert artifact["pages"][2]["text"] == ""
    assert all(
        page["text_characters"] == len(page["text"]) for page in artifact["pages"]
    )
    assert artifact["summary"] == result["summary"]


def test_extract_rejects_manual_review_route(tmp_path: Path) -> None:
    classification_path, cache_root, _, _, classification = _classification(tmp_path)
    classification["summary"]["proposed_route"] = "manual_review"
    classification_path.write_text(json.dumps(classification), encoding="utf-8")

    with pytest.raises(ExtractionError) as caught:
        extract(classification_path, cache_root=cache_root)

    assert caught.value.code == "unsupported_classification_route"
    assert not (cache_root / "extractions").exists()


def test_extract_merges_native_ocr_and_skipped_pages(tmp_path: Path) -> None:
    classification_path, cache_root, cached_pdf, _, classification = _classification(
        tmp_path
    )
    classification["summary"]["proposed_route"] = "hybrid"
    classification["pages"][1]["proposed_route"] = "ocr"
    classification["pages"][1]["reason_codes"] = [
        "dominant_raster",
        "no_extractable_text",
    ]
    classification_path.write_text(json.dumps(classification), encoding="utf-8")

    def fake_ocr(
        pdf_path: Path,
        page_numbers: list[int],
        actual_cache_root: Path,
        source_id: str,
    ) -> dict[str, object]:
        assert pdf_path == cached_pdf
        assert page_numbers == [2]
        assert actual_cache_root == cache_root
        assert source_id == classification["source_id"]
        return {
            "pages": {
                2: {
                    "text": "OCR cover text",
                    "result_path": "extraction-work/work/page-0002.json",
                }
            },
            "metadata": {"engine": "paddleocr", "engine_version": "3.7.0"},
            "checkpoints_reused": 0,
        }

    result = extract(classification_path, cache_root=cache_root, ocr_extractor=fake_ocr)

    artifact = json.loads(
        (cache_root / result["result_path"]).read_text(encoding="utf-8")
    )
    assert [page["method"] for page in artifact["pages"]] == [
        "native",
        "ocr",
        "skip",
        "native",
    ]
    assert artifact["pages"][1]["text"] == "OCR cover text"
    assert artifact["pages"][1]["ocr_detail_path"].endswith("page-0002.json")
    assert result["summary"]["native_pages"] == 2
    assert result["summary"]["ocr_pages"] == 1
    assert result["summary"]["skipped_pages"] == 1


def test_extract_rejects_changed_cached_bytes(tmp_path: Path) -> None:
    classification_path, cache_root, cached_pdf, _, _ = _classification(tmp_path)
    cached_pdf.write_bytes(b"changed")

    with pytest.raises(ExtractionError) as caught:
        extract(classification_path, cache_root=cache_root)

    assert caught.value.code == "cache_size_mismatch"
    assert not (cache_root / "extractions").exists()


def test_extract_rejects_mismatched_result_path(tmp_path: Path) -> None:
    classification_path, cache_root, _, _, classification = _classification(tmp_path)
    classification["result_path"] = "classifications/different.json"
    classification_path.write_text(json.dumps(classification), encoding="utf-8")

    with pytest.raises(ExtractionError) as caught:
        extract(classification_path, cache_root=cache_root)

    assert caught.value.code == "invalid_classification"
    assert not (cache_root / "extractions").exists()


def test_extract_discards_partial_page_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    classification_path, cache_root, _, _, _ = _classification(tmp_path)

    class Page:
        def __init__(self, pdf_page: int) -> None:
            self.pdf_page = pdf_page

        def extract_text(self) -> str:
            if self.pdf_page == 2:
                raise ValueError("broken text object")
            return "text"

    class Reader:
        is_encrypted = False

        def __init__(self, path: Path, *, strict: bool) -> None:
            assert path.suffix == ".pdf"
            assert strict is False
            self.pages = [Page(pdf_page) for pdf_page in range(1, 5)]

    monkeypatch.setattr(extract_module, "PdfReader", Reader)

    with pytest.raises(ExtractionError) as caught:
        extract(classification_path, cache_root=cache_root)

    assert caught.value.code == "page_extraction_failed"
    assert "page 2" in str(caught.value)
    assert not (cache_root / "extractions").exists()
