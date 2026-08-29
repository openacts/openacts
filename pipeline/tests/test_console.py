import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openacts_pipeline.common import PipelineError, local_clock
from openacts_pipeline.console import acts, review, state
from openacts_pipeline.console.app import _summarise, create_app
from openacts_pipeline.console.jobs import Job
from openacts_pipeline.console.registry import BY_NAME, artifacts
from openacts_pipeline.console.state import survey
from openacts_pipeline.corpus import CANDIDATE_VERSION, _ordered_provision_ids_sha256
from openacts_pipeline.corpus_files import act_relative_dir

SOURCE_ID = "sha256:" + "ab" * 32


def _receipt(cache_root: Path, digest: str, title: str) -> None:
    """A receipt is named for its request, never for the Source it fetched."""
    (cache_root / "runs").mkdir(parents=True, exist_ok=True)
    request_digest = digest[::-1]
    (cache_root / "runs" / f"20260828T000000Z-{request_digest}.json").write_text(
        json.dumps(
            {
                "status": "success",
                "source": {"source_id": f"sha256:{digest}" + "0" * 56,
                           "document_title": title},
            }
        ),
        encoding="utf-8",
    )


def _classification(cache_root: Path, digest: str, route: str, layer: str) -> None:
    (cache_root / "classifications").mkdir(parents=True, exist_ok=True)
    (cache_root / "classifications" / f"20260828T000000Z-{digest}-aaaa.json").write_text(
        json.dumps(
            {
                "page_count": 19,
                "summary": {"proposed_route": route, "proposed_text_layer": layer},
            }
        ),
        encoding="utf-8",
    )


def test_command_threads_a_second_input_from_its_own_folder() -> None:
    argv = BY_NAME["candidate"].command(
        Path("cache"), {"input": "s.json", "act": "ndpa-act.json"}, execute=False
    )
    assert argv == [
        "candidate",
        "cache/structures/s.json",
        "cache/acts/ndpa-act.json",
        "--cache-root",
        "cache",
    ]


def test_execute_is_only_appended_for_a_stage_that_has_one() -> None:
    values = {"input": "a.json"}
    assert "--execute" in BY_NAME["structure"].command(
        Path("cache"), values, execute=True
    )
    assert "--execute" not in BY_NAME["extract"].command(
        Path("cache"), values, execute=True
    )


def test_artifacts_are_listed_newest_first(tmp_path: Path) -> None:
    folder = tmp_path / "extractions"
    folder.mkdir()
    for index, name in enumerate(("old.json", "new.json")):
        path = folder / name
        path.write_text("{}", encoding="utf-8")
        import os

        os.utime(path, (index, index))
    assert artifacts(tmp_path, "extractions") == ["new.json", "old.json"]


def test_artifacts_of_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert artifacts(tmp_path, "structures") == []


def test_survey_reports_the_next_runnable_stage(tmp_path: Path) -> None:
    _receipt(tmp_path, "39a18d31", "NITDA Act, 2007")
    assert survey(tmp_path)[0].next_stage == "classify"
    _classification(tmp_path, "39a18d31", "extract", "born_digital")
    state = survey(tmp_path)[0]
    assert state.next_stage == "extract"
    assert state.pages == 19
    assert state.blocked is None


def test_a_source_the_classifier_refused_has_nothing_runnable(tmp_path: Path) -> None:
    """A scanned gazette routed to manual review is a state, not an error."""
    _receipt(tmp_path, "496d8ce3", "National Minimum Wage Act, 2019")
    _classification(tmp_path, "496d8ce3", "manual_review", "ocr")
    state = survey(tmp_path)[0]
    assert state.next_stage is None
    assert "manual review" in state.blocked


def test_summarise_keeps_the_keys_that_change(tmp_path: Path) -> None:
    line = _summarise(
        {
            "type": "progress",
            "timestamp": "2026-08-28T13:41:22.100000Z",
            "event": "audit_completed",
            "issues": 36,
            "usage": {"input_tokens": 1},
            "passed": False,
        }
    )
    assert line.startswith(local_clock("2026-08-28T13:41:22.100000Z"))
    assert "audit_completed" in line
    assert "issues=36" in line
    assert "usage" not in line


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    _receipt(tmp_path, "5bfb22cf", "Nigeria Data Protection Act, 2023")
    _classification(tmp_path, "5bfb22cf", "extract", "born_digital")
    # A stage with no available input renders no form at all, so every folder a
    # tested page reads from needs something in it.
    for folder in (
        "extractions",
        "structures",
        "corpus-candidates",
        "requests",
        "acts",
        "classifications",
    ):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
        (tmp_path / folder / "20260828T000000Z-5bfb22cf-aaaa.json").write_text(
            "{}", encoding="utf-8"
        )
    return TestClient(create_app(tmp_path, tmp_path))


def test_overview_lists_each_source_with_its_next_stage(client: TestClient) -> None:
    body = client.get("/").text
    assert "Nigeria Data Protection Act, 2023" in body
    assert "/stage/extract" in body


def test_stage_page_offers_execute_only_where_one_exists(client: TestClient) -> None:
    assert 'value="execute"' in client.get("/stage/structure").text
    assert 'value="execute"' not in client.get("/stage/extract").text


def test_promote_page_says_it_writes_the_corpus(client: TestClient) -> None:
    assert "writes to corpus" in client.get("/stage/promote").text


def test_unknown_stage_is_not_found(client: TestClient) -> None:
    assert client.get("/stage/nonsense").status_code == 404


def test_executing_a_stage_that_has_no_execute_is_refused(client: TestClient) -> None:
    response = client.post(
        "/stage/extract", data={"input": "a.json", "mode": "execute"}
    )
    assert response.status_code == 400


def test_a_chosen_input_must_be_one_the_cache_offers(client: TestClient) -> None:
    """The name becomes a filesystem path on a console that can write corpus."""
    for chosen in ("", "../../etc/passwd", "not-a-real-artifact.json"):
        response = client.post(
            "/stage/structure", data={"input": chosen, "mode": "dry-run"}
        )
        assert response.status_code == 400, chosen


def test_a_second_input_is_checked_too(client: TestClient) -> None:
    response = client.post(
        "/stage/candidate",
        data={
            "input": "20260828T000000Z-5bfb22cf-aaaa.json",
            "act": "../secrets.json",
            "mode": "dry-run",
        },
    )
    assert response.status_code == 400


def test_summarise_surfaces_a_failure_message() -> None:
    line = _summarise(
        {
            "status": "failure",
            "error": {"code": "promotion_blocked", "message": "target Act exists"},
        }
    )
    assert "promotion_blocked" in line
    assert "target Act exists" in line


def test_a_stage_that_reports_nothing_does_not_look_broken(client: TestClient) -> None:
    """Extraction finishes in seconds and emits no events; an empty pane reads
    as a failure rather than as a stage that had nothing to say."""
    redirect = client.post(
        "/stage/extract",
        data={"input": "20260828T000000Z-5bfb22cf-aaaa.json", "mode": "dry-run"},
        follow_redirects=False,
    ).headers["location"]
    body = client.get(redirect).text
    assert "reported no progress" in body or "No progress reported yet" in body


def _candidate(
    cache_root: Path,
    name: str,
    provisions: list[dict],
    audit: dict | None = None,
    structure: str = "structures/s.json",
) -> None:
    """Write a candidate that passes the real integrity checks.

    `review` re-validates everything `candidate` emitted, so a hand-rolled
    fixture that skips the manifest hash or the act directory is rejected before
    any verdict is recorded. The production helpers are used here so the fixture
    cannot drift from what the pipeline actually writes.
    """
    act = {
        "schema_version": "0.1.0",
        "record_type": "act",
        "act_id": "ng-federal-act-2007-x",
        "jurisdiction": "ng-federal",
        "country_code": "NG",
        "titles": {"official": "Test Act 2007", "short": "Test Act", "long": None},
        "year": 2007,
        "number": None,
        "citation": "Test Act 2007",
        "text_kind": "as_enacted",
        "dates": {
            key: {"date": None, "null_reason": "not_researched", "source_ids": []}
            for key in ("assent", "publication", "commencement", "repeal")
        },
        "aliases": [],
        "status": "unknown",
        "checked_through_date": None,
        "status_source_ids": [],
        "source_refs": [{"source_id": SOURCE_ID, "role": "authoritative_text",
                         "scope_note": None}],
        "editorial_notes": [],
    }
    source = {
        "schema_version": "0.1.0",
        "record_type": "source",
        "source_id": SOURCE_ID,
        "document_title": "Test Act 2007",
        "document_publisher": "Test",
        "language": "eng",
        "source_class": "institutional_copy",
        "publication": None,
        "media_type": "application/pdf",
        "byte_length": 1024,
        "page_count": 20,
        "text_layer": "born_digital",
        "locations": [{"url": "https://example.invalid/a.pdf",
                       "provider_name": "Test", "retrieved_at": "2026-08-28T00:00:00Z",
                       "http_last_modified": None, "notes": None}],
        "redistribution": {"status": "not_researched", "license": None, "notes": None},
        "document_notes": [],
    }
    root = cache_root / "corpus-candidates" / name
    act_dir = root / act_relative_dir(act)
    act_dir.mkdir(parents=True, exist_ok=True)
    (root / "candidate.json").write_text(
        json.dumps(
            {
                "candidate_version": CANDIDATE_VERSION,
                "act_id": act["act_id"],
                "source_id": SOURCE_ID,
                "input_structure": structure,
                "provision_count": len(provisions),
                "ordered_provision_ids_sha256": _ordered_provision_ids_sha256(
                    provisions
                ),
            }
        ),
        encoding="utf-8",
    )
    (root / "sources.jsonl").write_text(json.dumps(source) + "\n", encoding="utf-8")
    (act_dir / "act.json").write_text(json.dumps(act), encoding="utf-8")
    (act_dir / "citations.jsonl").write_text("", encoding="utf-8")
    (act_dir / "provisions.jsonl").write_text(
        "".join(json.dumps(p) + "\n" for p in provisions), encoding="utf-8"
    )
    if audit is not None:
        path = cache_root / structure
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"audit": audit}), encoding="utf-8")


def _provision(pid: str, text: str, page: int = 1, order: int = 1, **kwargs) -> dict:
    span = {"source_id": SOURCE_ID, "pdf_page": page}
    return {
        "schema_version": "0.1.0",
        "record_type": "provision",
        "provision_id": f"ng-federal-act-2007-x:{pid}",
        "node_type": kwargs.get("node_type", "section"),
        "display_label": kwargs.get("display_label"),
        "heading": kwargs.get("heading"),
        "parent_provision_id": None,
        "order": order,
        "source_spans": [span],
        "content_blocks": kwargs.get(
            "content_blocks",
            [{"block_id": "block-1", "kind": "text", "text": text,
              "source_spans": [span]}],
        ),
        "text_fidelity": kwargs.get("text_fidelity", "machine_extracted"),
    }


def test_a_variance_lands_on_the_provision_whose_text_it_quotes(
    tmp_path: Path,
) -> None:
    _candidate(
        tmp_path,
        "c",
        [
            _provision("section-1", "The Minister may give the Agency or the Board"),
            _provision("section-2", "Something entirely unrelated to that", order=2),
        ],
        {
            "issues": [],
            "variances": [
                {
                    "pdf_page": 1,
                    "source_line": 4,
                    "source_excerpt": "The Minister may give the Agency of the Board",
                    "draft_excerpt": "The Minister may give the Agency or the Board",
                    "varying_characters": 2,
                }
            ],
        },
    )
    view = review.load(tmp_path, "c")

    assert [p.short_id for p in view.attention] == ["section-1"]
    assert view.provisions[1].findings == []
    assert view.pages == []


def test_an_issue_quoting_the_draft_lands_on_its_provision(tmp_path: Path) -> None:
    _candidate(
        tmp_path,
        "c",
        [_provision("section-1", "A levy of one percent of the profit before tax")],
        {
            "issues": [
                {
                    "code": "unsupported_output",
                    "message": "content text is not an exact normalized source span",
                    "pdf_page": 1,
                    "source_excerpt": "A levy of one percent of the profit before tax",
                }
            ]
        },
    )
    view = review.load(tmp_path, "c")

    assert [p.short_id for p in view.attention] == ["section-1"]
    assert view.pages == []


def test_missing_source_groups_on_its_page_instead_of_every_provision(
    tmp_path: Path,
) -> None:
    """The excerpt is text the draft never produced, so no Provision can own it."""
    _candidate(
        tmp_path,
        "c",
        [
            _provision("section-1", "First provision text", page=9, order=1),
            _provision("section-2", "Second provision text", page=9, order=2),
            _provision("section-3", "Elsewhere entirely", page=12, order=3),
        ],
        {
            "issues": [
                {
                    "code": "missing_source",
                    "message": "72 normalized source characters are unclaimed",
                    "pdf_page": 9,
                    "source_excerpt": "and sources and use of the monies and assets",
                }
            ]
        },
    )
    view = review.load(tmp_path, "c")

    assert view.attention == []
    assert view.page_finding_count == 1
    assert len(view.pages) == 1
    page = view.pages[0]
    assert page.pdf_page == 9
    assert [p.short_id for p in page.provisions] == ["section-1", "section-2"]


def test_table_cell_text_is_read_so_a_table_provision_can_be_matched(
    tmp_path: Path,
) -> None:
    provision = _provision("schedule-1", "")
    provision["content_blocks"] = [
        {
            "block_id": "block-1",
            "kind": "table",
            "row_groups": [
                {
                    "rows": [
                        {
                            "cells": [
                                {"content_blocks": [{"kind": "text", "text": "Abia"}]},
                                {"content_blocks": [{"kind": "text", "text": "Aba"}]},
                            ]
                        }
                    ]
                }
            ],
        }
    ]
    _candidate(tmp_path, "c", [provision])
    view = review.load(tmp_path, "c")

    assert view.provisions[0].has_table
    assert view.provisions[0].text == "Abia Aba"


def test_a_candidate_whose_structure_is_gone_still_loads(tmp_path: Path) -> None:
    _candidate(tmp_path, "c", [_provision("section-1", "text")], audit=None)
    view = review.load(tmp_path, "c")

    assert view.audit_missing
    assert view.attention == []
    assert len(view.provisions) == 1


def test_remaining_counts_only_provisions_nobody_has_ruled_on(tmp_path: Path) -> None:
    _candidate(
        tmp_path,
        "c",
        [
            _provision("section-1", "a"),
            _provision("section-2", "b", order=2, text_fidelity="single_reviewed"),
        ],
    )
    view = review.load(tmp_path, "c")

    assert view.remaining == 1
    assert view.counts == {"machine_extracted": 1, "single_reviewed": 1}


def _reviewable(cache_root: Path) -> TestClient:
    _candidate(
        cache_root,
        "cand",
        [
            _provision("section-1", "The Agency or the Board may direct"),
            _provision("section-2", "Untouched by any finding", order=2),
        ],
        {
            "source_characters": 100,
            "claimed_characters": 99,
            "issues": [],
            "variances": [
                {
                    "pdf_page": 1,
                    "source_line": 4,
                    "source_excerpt": "The Agency of the Board may direct",
                    "draft_excerpt": "The Agency or the Board may direct",
                    "varying_characters": 2,
                }
            ],
        },
    )
    return TestClient(create_app(cache_root, cache_root))


def test_review_page_shows_the_variance_beside_the_provision(tmp_path: Path) -> None:
    body = _reviewable(tmp_path).get("/review/cand").text

    assert "Test Act 2007" in body
    assert "The Agency of the Board may direct" in body
    assert "99.00% of source claimed" in body
    assert "section-1" in body
    assert "section-2" not in body


def test_review_filters_choose_which_provisions_are_listed(tmp_path: Path) -> None:
    client = _reviewable(tmp_path)

    assert "section-2" in client.get("/review/cand?show=all").text
    assert "section-2" in client.get("/review/cand?show=unreviewed").text
    assert client.get("/review/cand?show=nonsense").status_code == 400


def test_recording_one_verdict_leaves_the_other_provision_alone(
    tmp_path: Path,
) -> None:
    client = _reviewable(tmp_path)
    response = client.post(
        "/review/cand",
        data={"provision": "ng-federal-act-2007-x:section-1",
              "fidelity": "source_conflict", "show": "all"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/review/cand?show=all"
    view = review.load(tmp_path, "cand")
    assert view.provisions[0].text_fidelity == "source_conflict"
    assert view.provisions[1].text_fidelity == "machine_extracted"


def test_the_bulk_verdict_covers_every_remaining_provision(tmp_path: Path) -> None:
    client = _reviewable(tmp_path)
    client.post("/review/cand", data={"fidelity": "single_reviewed"})

    view = review.load(tmp_path, "cand")
    assert view.remaining == 0
    assert view.counts == {"single_reviewed": 2}


def test_a_whole_candidate_verdict_other_than_single_reviewed_is_refused(
    tmp_path: Path,
) -> None:
    """`review` reserves the other verdicts for a named Provision."""
    client = _reviewable(tmp_path)
    response = client.post("/review/cand", data={"fidelity": "double_reviewed"})

    assert response.status_code == 400
    assert review.load(tmp_path, "cand").remaining == 2


def test_a_candidate_outside_the_cache_listing_is_refused(tmp_path: Path) -> None:
    client = _reviewable(tmp_path)

    assert client.get("/review/..%2F..%2Fetc").status_code >= 400
    assert client.get("/review/nope").status_code == 400
    refused = client.post("/review/nope", data={"fidelity": "single_reviewed"})
    assert refused.status_code == 400


def test_every_stage_cell_reports_the_same_three_states(tmp_path: Path) -> None:
    """The old table put a route in one column and a sentence in another."""
    _receipt(tmp_path, "5bfb22cf", "Nigeria Data Protection Act, 2023")
    _classification(tmp_path, "5bfb22cf", "extract", "born_digital")
    (tmp_path / "extractions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "extractions" / "20260828T000000Z-5bfb22cf-aaaa.json").write_text(
        "{}", encoding="utf-8"
    )

    row = survey(tmp_path)[0]

    assert set(row.stages.values()) <= {"done", "blocked"}
    assert row.status("classify") == "done"
    assert row.status("extract") == "done"
    assert row.status("structure") == ""
    assert row.next_stage == "structure"
    assert row.reached == "extract"
    assert row.progress == 2
    # The route is detail about classification, not the state of it.
    assert row.detail["classify"] == "route extract"


def test_a_manual_review_route_blocks_the_row(tmp_path: Path) -> None:
    _receipt(tmp_path, "496d8ce3", "National Minimum Wage Act, 2019")
    _classification(tmp_path, "496d8ce3", "manual_review", "ocr")

    row = survey(tmp_path)[0]

    assert row.status("classify") == "blocked"
    assert row.next_stage is None
    assert row.blocked


def test_an_act_in_the_corpus_reads_as_finished_without_a_candidate(
    tmp_path: Path,
) -> None:
    """Promotion outlives the candidate, so the corpus is what proves it."""
    _receipt(tmp_path, "5bfb22cf", "Nigeria Data Protection Act, 2023")
    _classification(tmp_path, "5bfb22cf", "extract", "born_digital")
    corpus = tmp_path / "corpus" / "ng" / "federal" / "acts" / "2023" / "37"
    corpus.mkdir(parents=True)
    (corpus / "act.json").write_text(
        json.dumps(
            {
                "act_id": "ng-federal-act-2023-37",
                "source_refs": [{"source_id": "sha256:5bfb22cf" + "0" * 56}],
            }
        ),
        encoding="utf-8",
    )

    row = survey(tmp_path, tmp_path / "corpus")[0]

    assert row.in_corpus
    assert row.next_stage is None
    assert row.progress == 6
    assert row.act_id == "ng-federal-act-2023-37"


def test_adding_a_source_writes_a_request_and_starts_acquiring(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(tmp_path, tmp_path))
    response = client.post(
        "/sources",
        data={
            "url": "https://nass.gov.ng/documents/download/11251",
            "document_title": "Nigeria Revenue Service (Establishment) Act, 2025",
            "document_publisher": "Federal Republic of Nigeria",
            "provider_name": "National Assembly of Nigeria",
            "source_class": "official_gazette",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs/")
    written = list((tmp_path / "requests").glob("*.json"))
    assert len(written) == 1
    assert written[0].name == "nigeria-revenue-service-establishment-act-2025.json"
    assert json.loads(written[0].read_text()) == {
        "url": "https://nass.gov.ng/documents/download/11251",
        "provider_name": "National Assembly of Nigeria",
        "document_title": "Nigeria Revenue Service (Establishment) Act, 2025",
        "document_publisher": "Federal Republic of Nigeria",
        "source_class": "official_gazette",
    }


def test_adding_a_source_never_overwrites_an_existing_request(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(tmp_path, tmp_path))
    payload = {
        "url": "https://example.invalid/a.pdf",
        "document_title": "Test Act 2007",
        "document_publisher": "Federal Republic of Nigeria",
        "provider_name": "Test",
        "source_class": "official_gazette",
    }
    client.post("/sources", data=payload)
    client.post("/sources", data={**payload, "url": "https://example.invalid/b.pdf"})

    written = sorted(path.name for path in (tmp_path / "requests").glob("*.json"))
    assert written == ["test-act-2007-2.json", "test-act-2007.json"]


def test_an_incomplete_or_invented_source_is_refused(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, tmp_path))
    complete = {
        "url": "https://example.invalid/a.pdf",
        "document_title": "Test Act 2007",
        "document_publisher": "Federal Republic of Nigeria",
        "provider_name": "Test",
        "source_class": "official_gazette",
    }

    assert client.post("/sources", data={"url": "x"}).status_code == 400
    bad = {**complete, "source_class": "made_up"}
    assert client.post("/sources", data=bad).status_code == 400
    assert not list((tmp_path / "requests").glob("*.json"))


def test_the_overview_filter_selects_rows(tmp_path: Path) -> None:
    _receipt(tmp_path, "5bfb22cf", "Nigeria Data Protection Act, 2023")
    _classification(tmp_path, "5bfb22cf", "extract", "born_digital")
    _receipt(tmp_path, "496d8ce3", "National Minimum Wage Act, 2019")
    _classification(tmp_path, "496d8ce3", "manual_review", "ocr")
    client = TestClient(create_app(tmp_path, tmp_path))

    assert "Minimum Wage" in client.get("/?show=all").text
    assert "Minimum Wage" not in client.get("/?show=needs+action").text
    assert "Data Protection" not in client.get("/?show=blocked").text
    assert client.get("/?show=nonsense").status_code == 400


def test_the_act_form_is_prefilled_from_the_acquisition_receipt(
    tmp_path: Path,
) -> None:
    _receipt(tmp_path, "39a18d31", "National Information Technology Agency Act, 2007")
    body = TestClient(create_app(tmp_path, tmp_path)).get(
        "/acts/new?digest=39a18d31"
    ).text

    assert 'value="National Information Technology Agency Act, 2007"' in body
    assert 'value="2007"' in body


def test_a_saved_act_record_validates_against_the_corpus_schema(
    tmp_path: Path,
) -> None:
    """`candidate` rejects anything the Act schema does not accept."""
    from openacts_pipeline.corpus_schema import validate_record

    _receipt(tmp_path, "39a18d31", "NITDA Act, 2007")
    client = TestClient(create_app(tmp_path, tmp_path))
    response = client.post(
        "/acts",
        data={
            "digest": "39a18d31",
            "official_title": "National Information Technology Development Agency Act 2007",
            "short_title": "NITDA Act 2007",
            "year": "2007",
            "number": "",
            "slug": "nitda",
            "jurisdiction": "ng-federal",
            "text_kind": "as_enacted",
            "status": "unknown",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    written = tmp_path / "acts" / "ng-federal-act-2007-nitda.json"
    record = json.loads(written.read_text())
    validate_record("act", record)
    assert record["act_id"] == "ng-federal-act-2007-nitda"
    assert record["source_refs"][0]["source_id"] == "sha256:39a18d31" + "0" * 56
    assert record["titles"]["short"] == "NITDA Act 2007"
    assert record["dates"]["assent"]["null_reason"] == "not_researched"


def test_a_gazette_number_names_the_act_the_way_the_corpus_does(
    tmp_path: Path,
) -> None:
    draft = acts.draft_from_receipt(
        {"source": {"source_id": "sha256:x", "document_title": "Data Protection Act, 2023"}}
    )
    assert draft.year == 2023

    record = acts.build(
        {
            "official_title": "Nigeria Data Protection Act, 2023",
            "year": "2023",
            "number": "37",
            "slug": "37",
            "jurisdiction": "ng-federal",
            "text_kind": "as_enacted",
            "status": "in_force",
            "checked_through_date": "2026-08-29",
        },
        "sha256:abc",
    )
    assert record["act_id"] == "ng-federal-act-2023-37"
    assert record["number"] == "37"


def test_an_act_record_the_schema_would_reject_is_refused(tmp_path: Path) -> None:
    _receipt(tmp_path, "39a18d31", "NITDA Act, 2007")
    client = TestClient(create_app(tmp_path, tmp_path))
    complete = {
        "digest": "39a18d31",
        "official_title": "NITDA Act 2007",
        "year": "2007",
        "slug": "nitda",
        "jurisdiction": "ng-federal",
        "text_kind": "as_enacted",
        "status": "unknown",
    }

    assert client.post("/acts", data={**complete, "year": "soon"}).status_code == 400
    assert client.post("/acts", data={**complete, "jurisdiction": "NG"}).status_code == 400
    assert client.post("/acts", data={**complete, "text_kind": "invented"}).status_code == 400
    assert client.post("/acts", data={**complete, "official_title": " "}).status_code == 400
    assert client.post("/acts", data={**complete, "digest": "ffffffff"}).status_code == 404
    assert not (tmp_path / "acts").exists()


def test_the_backend_is_chosen_per_run_not_baked_into_the_console() -> None:
    stage = BY_NAME["structure"]
    values = {"input": "e.json", "backend": "codex"}

    # The backend is an environment choice, so it must not reach argv.
    assert "codex" not in stage.command(Path("cache"), values, execute=True)
    assert stage.environment(values)["OPENACTS_MODEL_BACKEND"] == "codex"


def test_the_backend_choice_carries_nothing_the_config_derives() -> None:
    """The timeout a backend needs is config's to know, not the form's."""
    stage = BY_NAME["structure"]

    assert stage.environment({"backend": "codex"}) == {
        "OPENACTS_MODEL_BACKEND": "codex"
    }


def test_the_structure_page_offers_every_configured_backend(
    client: TestClient,
) -> None:
    from openacts_pipeline.config import MODEL_BACKENDS

    body = client.get("/stage/structure").text
    for backend in MODEL_BACKENDS:
        assert f'value="{backend}"' in body


def test_an_invented_backend_is_refused(client: TestClient) -> None:
    response = client.post(
        "/stage/structure",
        data={
            "input": "20260828T000000Z-5bfb22cf-aaaa.json",
            "backend": "gpt-9",
            "mode": "dry-run",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400


@pytest.fixture
def in_zone(monkeypatch: pytest.MonkeyPatch):
    """Run a test as if the operator sat in a named zone."""

    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield use
    time.tzset()


def test_progress_times_are_shown_on_the_operators_clock(in_zone) -> None:
    """A UTC stamp with its Z sliced off reads as local when it is not."""
    event = {
        "timestamp": "2026-08-28T13:41:22.100000Z",
        "event": "audit_completed",
        "issues": 36,
    }

    in_zone("Africa/Lagos")
    assert _summarise(event).startswith("14:41:22")

    in_zone("UTC")
    assert _summarise(event).startswith("13:41:22")

    in_zone("America/New_York")
    assert _summarise(event).startswith("09:41:22")


def test_a_job_is_dated_on_the_operators_clock_too(in_zone) -> None:
    """The jobs list dates a run only by the UTC stamp in its name."""
    job = Job("20260828T134122Z-abc123", "structure", [], Path("."), execute=True)

    in_zone("Africa/Lagos")
    assert job.started == "14:41:22"

    in_zone("UTC")
    assert job.started == "13:41:22"


def test_an_unreadable_timestamp_renders_as_nothing_rather_than_crashing() -> None:
    assert local_clock("") == ""
    assert local_clock("not a timestamp") == ""
    assert _summarise({"event": "started"}).strip().startswith("started")
    assert Job("nonsense", "s", [], Path("."), execute=True).started == ""


def test_asserting_a_status_requires_the_evidence_the_schema_demands() -> None:
    """Any status but `unknown` needs a checked-through date and a source.

    The form offered six statuses and always wrote a null date and no sources,
    so five of them produced a record that could only fail at `candidate`.
    """
    from openacts_pipeline.corpus_schema import validate_record

    answers = {
        "official_title": "Electoral Act, 2026",
        "year": "2026",
        "number": "40",
        "slug": "electoral",
        "jurisdiction": "ng-federal",
        "text_kind": "as_enacted",
        "status": "in_force",
    }

    with pytest.raises(PipelineError, match="checked through"):
        acts.build(answers, SOURCE_ID)

    record = acts.build(
        {**answers, "checked_through_date": "2026-08-29"}, SOURCE_ID
    )
    validate_record("act", record)
    assert record["checked_through_date"] == "2026-08-29"
    assert record["status_source_ids"] == [SOURCE_ID]


def test_an_unknown_status_carries_no_date_or_sources() -> None:
    from openacts_pipeline.corpus_schema import validate_record

    record = acts.build(
        {
            "official_title": "Electoral Act, 2026",
            "year": "2026",
            "slug": "electoral",
            "jurisdiction": "ng-federal",
            "text_kind": "as_enacted",
            "status": "unknown",
        },
        SOURCE_ID,
    )

    validate_record("act", record)
    assert record["checked_through_date"] is None
    assert record["status_source_ids"] == []


def test_a_checked_through_date_must_look_like_a_date() -> None:
    with pytest.raises(PipelineError, match="checked through"):
        acts.build(
            {
                "official_title": "Electoral Act, 2026",
                "year": "2026",
                "slug": "electoral",
                "jurisdiction": "ng-federal",
                "text_kind": "as_enacted",
                "status": "in_force",
                "checked_through_date": "last Tuesday",
            },
            SOURCE_ID,
        )


def test_the_classify_dropdown_names_the_document_each_receipt_fetched(
    tmp_path: Path,
) -> None:
    _receipt(tmp_path, "5bfb22cf", "Nigeria Data Protection Act, 2023")
    _receipt(tmp_path, "39a18d31", "NITDA Act, 2007")

    client = TestClient(create_app(tmp_path, tmp_path))
    body = client.get("/stage/classify").text

    assert "Nigeria Data Protection Act, 2023" in body
    assert "NITDA Act, 2007" in body


def test_an_artifact_named_for_its_source_still_labels_by_digest(
    tmp_path: Path,
) -> None:
    _receipt(tmp_path, "5bfb22cf", "Nigeria Data Protection Act, 2023")
    _classification(tmp_path, "5bfb22cf", "born_digital_text", "born_digital")

    names = state.labels(tmp_path)
    receipt = "20260828T000000Z-fc22bfb5.json"
    classification = "20260828T000000Z-5bfb22cf-aaaa.json"

    assert state.label_for(receipt, names).startswith("Nigeria Data Protection Act")
    assert state.label_for(classification, names).startswith(
        "Nigeria Data Protection Act"
    )
    assert state.label_for("nothing-here.json", names) == "nothing-here.json"


def test_a_stage_with_no_execute_mode_is_not_called_a_dry_run(tmp_path: Path) -> None:
    candidate = Job("j1", "candidate", [], tmp_path, execute=False)
    structure = Job("j2", "structure", [], tmp_path, execute=False)

    assert not BY_NAME["candidate"].executable
    assert candidate.mode != "dry run"
    assert structure.mode == "dry run"
    assert Job("j3", "structure", [], tmp_path, execute=True).mode == "execute"
