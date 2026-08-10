import hashlib
import json
from pathlib import Path

import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.corpus import candidate, promote, review

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/valid"
FIXTURE_SOURCE_ID = "sha256:" + "a" * 64


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _replace_source_id(record: dict, source_id: str) -> dict:
    return json.loads(json.dumps(record).replace(FIXTURE_SOURCE_ID, source_id))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _pipeline(tmp_path: Path) -> tuple[Path, Path, Path]:
    cache_root = tmp_path / "source-cache"
    pdf_bytes = b"stable cached source bytes"
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    source_id = f"sha256:{digest}"
    pdf_path = cache_root / f"sha256/{digest[:2]}/{digest}.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(pdf_bytes)

    source = _replace_source_id(_load("source.json"), source_id)
    source["byte_length"] = len(pdf_bytes)
    source["page_count"] = 1
    source["text_layer"] = "unknown"
    receipt = cache_root / "runs/receipt.json"
    _write_json(
        receipt,
        {
            "status": "success",
            "cache_path": f"sha256/{digest[:2]}/{digest}.pdf",
            "source": source,
        },
    )
    classification = cache_root / "classifications/classification.json"
    _write_json(
        classification,
        {
            "stage": "classify",
            "status": "success",
            "source_id": source_id,
            "input_receipt": "runs/receipt.json",
            "summary": {"proposed_text_layer": "born_digital"},
        },
    )
    extraction = cache_root / "extractions/extraction.json"
    _write_json(
        extraction,
        {
            "stage": "extract",
            "status": "success",
            "source_id": source_id,
            "input_classification": "classifications/classification.json",
        },
    )

    provision = _replace_source_id(_load("provision.json"), source_id)

    def draft(
        draft_id: str,
        node_type: str,
        label: str | None,
        parent: str | None,
        order: int,
        text: str = "See section 1.",
    ) -> dict:
        blocks = json.loads(json.dumps(provision["content_blocks"]))
        blocks[0]["text"] = text
        return {
            "draft_id": draft_id,
            "node_type": node_type,
            "display_label": label,
            "heading": None,
            "parent_draft_id": parent,
            "order": order,
            "source_spans": provision["source_spans"],
            "content_blocks": blocks,
            "text_fidelity": "machine_extracted",
        }

    structure = cache_root / "structures/structure.json"
    _write_json(
        structure,
        {
            "stage": "structure",
            "status": "success",
            "source_id": source_id,
            "input_extraction": "extractions/extraction.json",
            "provisions": [
                draft("node-1", "part", "PART I", None, 1),
                draft("node-2", "section", "1.", "node-1", 1),
                draft("node-3", "subsection", "(1)", "node-2", 1),
                draft("node-3a", "paragraph", "(a)", "node-3", 1),
                draft("node-3b", "paragraph", "(b)", "node-3", 2),
                draft("node-3c", "paragraph", None, "node-3", 3),
                draft(
                    "node-4",
                    "definition",
                    None,
                    "node-2",
                    2,
                    '"term" means an example;',
                ),
                draft("node-5", "schedule", "SCHEDULE", None, 2),
                draft("node-6", "schedule_paragraph", "1.", "node-5", 1),
                draft("node-7", "schedule_subparagraph", "(1)", "node-6", 1),
                draft("node-8", "paragraph", "(a)", "node-7", 1),
                draft("node-9", "subparagraph", "(i)", "node-8", 1),
                draft("node-10", "schedule", "SECOND SCHEDULE", None, 3),
                draft("node-11", "schedule_paragraph", "1.", "node-10", 1),
            ],
        },
    )
    act = _replace_source_id(_load("act.json"), source_id)
    act_path = cache_root / "requests/act.json"
    _write_json(act_path, act)
    return cache_root, structure, act_path


def test_candidate_is_deterministic_and_promotion_requires_review(
    tmp_path: Path,
) -> None:
    cache_root, structure, act_path = _pipeline(tmp_path)
    first = candidate(structure, act_path, cache_root=cache_root)
    second = candidate(structure, act_path, cache_root=cache_root)

    assert first["candidate_path"] == second["candidate_path"]
    assert second["reused"] is True
    candidate_path = cache_root / first["candidate_path"]
    manifest = json.loads((candidate_path / "candidate.json").read_text())
    assert manifest == {
        "candidate_version": 1,
        "act_id": "ng-federal-act-2023-37",
        "source_id": first["source_id"],
        "input_structure": "structures/structure.json",
        "provision_count": 14,
        "ordered_provision_ids_sha256": manifest["ordered_provision_ids_sha256"],
    }
    assert manifest["ordered_provision_ids_sha256"].startswith("sha256:")
    provisions_path = next(candidate_path.glob("**/provisions.jsonl"))
    provisions = [json.loads(line) for line in provisions_path.read_text().splitlines()]
    assert [record["provision_id"].split(":", 1)[1] for record in provisions] == [
        "part-1",
        "section-1",
        "section-1.subsection-1",
        "section-1.subsection-1.paragraph-a",
        "section-1.subsection-1.paragraph-b",
        "section-1.subsection-1.paragraph-unnumbered-3",
        "section-1.definition-term",
        "schedule-1",
        "schedule-1.paragraph-1",
        "schedule-1.paragraph-1.subparagraph-1",
        "schedule-1.paragraph-1.subparagraph-1.paragraph-a",
        "schedule-1.paragraph-1.subparagraph-1.paragraph-a.subparagraph-i",
        "schedule-2",
        "schedule-2.paragraph-1",
    ]
    assert not any("~" in record["provision_id"] for record in provisions)
    assert promote(candidate_path, cache_root=cache_root)["ready"] is False
    with pytest.raises(PipelineError, match="machine_extracted"):
        promote(candidate_path, execute=True, cache_root=cache_root)

    before_review = provisions_path.read_bytes()
    review_preview = review(candidate_path, "single_reviewed")
    assert review_preview["status"] == "ready"
    assert review_preview["changed_provisions"] == 14
    assert review_preview["before_fidelity"] == {"machine_extracted": 14}
    assert review_preview["after_fidelity"] == {"single_reviewed": 14}
    assert provisions_path.read_bytes() == before_review

    review_result = review(candidate_path, "single_reviewed", execute=True)
    assert review_result["status"] == "success"
    reviewed = [json.loads(line) for line in provisions_path.read_text().splitlines()]
    assert [record["provision_id"] for record in reviewed] == [
        record["provision_id"] for record in provisions
    ]
    assert all(record["text_fidelity"] == "single_reviewed" for record in reviewed)
    assert [
        {key: value for key, value in record.items() if key != "text_fidelity"}
        for record in reviewed
    ] == [
        {key: value for key, value in record.items() if key != "text_fidelity"}
        for record in provisions
    ]
    assert review(candidate_path, "single_reviewed", execute=True)["reused"] is True

    with pytest.raises(PipelineError) as invalid_fidelity:
        review(candidate_path, "double_reviewed")
    assert invalid_fidelity.value.code == "invalid_review_fidelity"

    corpus_root = tmp_path / "corpus"
    source_bytes = (candidate_path / "sources.jsonl").read_bytes()
    corpus_root.mkdir()
    (corpus_root / "sources.jsonl").write_bytes(source_bytes)

    preview = promote(candidate_path, cache_root=cache_root, corpus_root=corpus_root)
    assert preview["ready"] is True
    result = promote(
        candidate_path,
        execute=True,
        cache_root=cache_root,
        corpus_root=corpus_root,
    )
    assert result["status"] == "success"
    assert (corpus_root / "ng/federal/acts/2023/37/act.json").exists()
    assert (corpus_root / "sources.jsonl").read_bytes() == source_bytes


def test_promotion_rejects_a_truncated_candidate(tmp_path: Path) -> None:
    cache_root, structure, act_path = _pipeline(tmp_path)
    result = candidate(structure, act_path, cache_root=cache_root)
    candidate_path = cache_root / result["candidate_path"]
    provisions_path = next(candidate_path.glob("**/provisions.jsonl"))
    provisions = [json.loads(line) for line in provisions_path.read_text().splitlines()]
    for provision in provisions[:3]:
        provision["text_fidelity"] = "single_reviewed"
    provisions_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in provisions[:3]
        ),
        encoding="utf-8",
    )

    with pytest.raises(PipelineError) as error:
        promote(candidate_path, cache_root=cache_root)
    assert error.value.code == "candidate_integrity_failed"
    assert str(error.value) == "expected 14 provisions, found 3"
