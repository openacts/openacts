import hashlib
import json
from pathlib import Path

import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.corpus import (
    _local_provision_id,
    candidate,
    load_corpus,
    promote,
    review,
)

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


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8"
    )


def test_load_corpus_validates_complete_canonical_layout(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    act_dir = corpus_root / "ng/federal/acts/2023/37"
    _write_json(act_dir / "act.json", _load("act.json"))
    _write_jsonl(corpus_root / "sources.jsonl", [_load("source.json")])
    _write_jsonl(act_dir / "provisions.jsonl", [_load("provision.json")])
    _write_jsonl(act_dir / "citations.jsonl", [_load("citation.json")])

    records = load_corpus(corpus_root, schema_dir=ROOT / "schemas")
    assert len(records.acts) == 1
    assert len(records.provisions) == 1
    assert len(records.sources) == 1
    assert len(records.citations) == 1
    assert records.schema_versions == ("0.1.0",)
    assert records.act_directories == (Path("ng/federal/acts/2023/37"),)

    later_source = dict(_load("source.json"))
    later_source["source_id"] = "sha256:" + "b" * 64
    _write_jsonl(
        corpus_root / "sources.jsonl", [later_source, _load("source.json")]
    )
    with pytest.raises(PipelineError) as unsorted:
        load_corpus(corpus_root, schema_dir=ROOT / "schemas")
    assert unsorted.value.code == "invalid_corpus"
    assert "sorted by source_id" in str(unsorted.value)

    _write_jsonl(corpus_root / "sources.jsonl", [_load("source.json")])
    (act_dir / "notes.txt").write_text("not canonical", encoding="utf-8")
    with pytest.raises(PipelineError) as unexpected_file:
        load_corpus(corpus_root, schema_dir=ROOT / "schemas")
    assert unexpected_file.value.code == "invalid_corpus"
    assert "unexpected" in str(unexpected_file.value)
    (act_dir / "notes.txt").unlink()

    with pytest.raises(PipelineError) as missing_schemas:
        load_corpus(corpus_root, schema_dir=tmp_path / "missing-schemas")
    assert missing_schemas.value.code == "invalid_corpus_schema"

    invalid_act = _load("act.json")
    invalid_act["record_type"] = "source"
    _write_json(act_dir / "act.json", invalid_act)
    with pytest.raises(PipelineError) as invalid_record:
        load_corpus(corpus_root, schema_dir=ROOT / "schemas")
    assert invalid_record.value.code == "invalid_corpus_record"


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


def test_review_records_one_provision_without_touching_the_rest(
    tmp_path: Path,
) -> None:
    """A reviewer adjudicates a finding against one Provision, not a whole Act."""
    cache_root, structure, act_path = _pipeline(tmp_path)
    created = candidate(structure, act_path, cache_root=cache_root)
    candidate_path = cache_root / created["candidate_path"]
    provisions_path = next(candidate_path.glob("**/provisions.jsonl"))
    target = "ng-federal-act-2023-37:section-1"

    preview = review(candidate_path, "source_conflict", provision_id=target)
    assert preview["status"] == "ready"
    assert preview["changed_provisions"] == 1
    assert preview["after_fidelity"] == {
        "machine_extracted": 13,
        "source_conflict": 1,
    }

    review(candidate_path, "source_conflict", provision_id=target, execute=True)
    recorded = [json.loads(line) for line in provisions_path.read_text().splitlines()]
    verdicts = {record["provision_id"]: record["text_fidelity"] for record in recorded}
    assert verdicts[target] == "source_conflict"
    assert set(verdicts.values()) == {"machine_extracted", "source_conflict"}


def test_a_per_provision_verdict_may_be_any_reviewed_value(tmp_path: Path) -> None:
    cache_root, structure, act_path = _pipeline(tmp_path)
    candidate_path = cache_root / candidate(
        structure, act_path, cache_root=cache_root
    )["candidate_path"]
    target = "ng-federal-act-2023-37:section-1"
    for verdict in ("single_reviewed", "double_reviewed", "source_conflict"):
        result = review(candidate_path, verdict, provision_id=target, execute=True)
        assert result["status"] == "success"


def test_a_whole_candidate_review_still_refuses_anything_but_single(
    tmp_path: Path,
) -> None:
    """Asserting double review across an Act nobody read twice is not a thing."""
    cache_root, structure, act_path = _pipeline(tmp_path)
    candidate_path = cache_root / candidate(
        structure, act_path, cache_root=cache_root
    )["candidate_path"]
    with pytest.raises(PipelineError, match="single_reviewed"):
        review(candidate_path, "double_reviewed")


def test_an_unknown_provision_is_refused(tmp_path: Path) -> None:
    cache_root, structure, act_path = _pipeline(tmp_path)
    candidate_path = cache_root / candidate(
        structure, act_path, cache_root=cache_root
    )["candidate_path"]
    with pytest.raises(PipelineError, match="not in this candidate"):
        review(candidate_path, "single_reviewed", provision_id="ng-federal-act:nope")


def _unlabelled(node_type: str, order: int = 1) -> dict:
    return {
        "draft_id": "node-1",
        "node_type": node_type,
        "display_label": None,
        "heading": None,
        "order": order,
        "content_blocks": [],
    }


@pytest.mark.parametrize("node_type", ["form", "table", "cross_heading"])
def test_a_node_named_by_its_parent_is_placed_by_order(node_type: str) -> None:
    assert (
        _local_provision_id(_unlabelled(node_type), "schedule-14")
        == f"schedule-14.{node_type.replace('_', '-')}-unnumbered-1"
    )


@pytest.mark.parametrize(
    "node_type", ["part", "chapter", "division", "section", "schedule_part"]
)
def test_a_node_that_must_carry_a_number_is_still_refused(node_type: str) -> None:
    with pytest.raises(PipelineError, match="no stable label"):
        _local_provision_id(_unlabelled(node_type), "part-1")


def _definition(text: str, order: int = 1) -> dict:
    return {
        "draft_id": "node-0561",
        "node_type": "definition",
        "display_label": None,
        "heading": None,
        "order": order,
        "content_blocks": [{"kind": "text", "text": text}],
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"trustee" means the person in whom the property is invested;', "trustee"),
        ("“trustee” means the person in whom the property is invested;", "trustee"),
        ('trustee" under a collective investment scheme means the person;', "trustee"),
    ],
)
def test_a_definition_is_named_by_its_term_despite_a_damaged_quotation(
    text: str, expected: str
) -> None:
    assert (
        _local_provision_id(_definition(text), "section-63.subsection-5")
        == f"section-63.subsection-5.definition-{expected}"
    )
