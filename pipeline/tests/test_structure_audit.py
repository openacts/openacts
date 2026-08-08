from openacts_pipeline.structure_audit import audit_drafts
from openacts_pipeline.structure_schema import StructureDraft


def _draft(*, include_paragraph: bool) -> StructureDraft:
    children = []
    if include_paragraph:
        children.append(
            {
                "node_type": "paragraph",
                "display_label": "(a)",
                "pdf_page": 1,
                "content_blocks": [
                    {
                        "kind": "text",
                        "text": "the first complete paragraph.",
                        "pdf_pages": [1],
                    }
                ],
            }
        )
    return StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Completeness",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "The parent provides —",
                            "pdf_pages": [1],
                        }
                    ],
                    "children": children,
                }
            ]
        }
    )


def test_source_claim_audit_requires_every_operative_character_and_marker() -> None:
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "1. Completeness\n"
                "The parent provides —\n"
                "(a) the first complete paragraph.\n"
                "[Section 2]"
            ),
        }
    ]

    complete = audit_drafts(
        [("body-01", _draft(include_paragraph=True))],
        pages=pages,
        legal_start_pdf_page=1,
        legal_end_pdf_page=1,
    )
    incomplete = audit_drafts(
        [("body-01", _draft(include_paragraph=False))],
        pages=pages,
        legal_start_pdf_page=1,
        legal_end_pdf_page=1,
    )

    assert complete.passed
    assert complete.claimed_characters == complete.source_characters
    assert [exclusion.reason for exclusion in complete.exclusions] == [
        "editorial_annotation"
    ]
    assert not incomplete.passed
    assert {issue.code for issue in incomplete.issues} >= {
        "missing_marker",
        "missing_source",
    }


def test_source_claim_audit_excludes_variable_gazette_headers() -> None:
    words = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
    pages = []
    nodes = []
    for page, word in enumerate(words, start=1):
        header = (
            f"2022 No. 37 A 72{page} Example Act"
            if page % 2
            else f"A 72{page} 2023 No. 37 Example Act"
        )
        body_echo = "2040 No. 37 A 999 Example Act" if page == 1 else None
        pages.append(
            {
                "pdf_page": page,
                "text": (
                    f"{header}\n{page}. {word}\nUnique {word} wording."
                    + (f"\n\n{body_echo}" if body_echo else "")
                ),
            }
        )
        content_blocks = [
            {
                "kind": "text",
                "text": f"Unique {word} wording.",
                "pdf_pages": [page],
            }
        ]
        if body_echo:
            content_blocks.append(
                {"kind": "text", "text": body_echo, "pdf_pages": [page]}
            )
        nodes.append(
            {
                "node_type": "section",
                "display_label": f"{page}.",
                "heading": word,
                "pdf_page": page,
                "content_blocks": content_blocks,
            }
        )

    report = audit_drafts(
        [("body", StructureDraft.model_validate({"nodes": nodes}))],
        pages=pages,
        legal_start_pdf_page=1,
        legal_end_pdf_page=6,
    )

    assert report.passed
    assert [exclusion.reason for exclusion in report.exclusions].count(
        "recurring_header"
    ) == 6


def test_source_claim_audit_excludes_bracketed_legal_annotations() -> None:
    page = {
        "pdf_page": 1,
        "text": (
            "1. Cross references\nOperative wording.\n"
            "SCHEDULES\n"
            "[Fourth Schedule]\n[Sections 3 and 297]\n"
            "[Item D, Part II Second Schedule]\n[Cap. C20 LFN]\n[1991 No. 24]"
        ),
    }
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Cross references",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "Operative wording.",
                            "pdf_pages": [1],
                        }
                    ],
                }
            ]
        }
    )

    report = audit_drafts(
        [("body", draft)],
        pages=[page],
        legal_start_pdf_page=1,
        legal_end_pdf_page=1,
    )

    assert report.passed
    assert [exclusion.reason for exclusion in report.exclusions].count(
        "editorial_annotation"
    ) == 6


def test_source_claim_audit_rejects_partially_overlapping_claims() -> None:
    page = {"pdf_page": 1, "text": "1. Example\nA complete provision."}
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Example",
                    "pdf_page": 1,
                    "content_blocks": [
                        {"kind": "text", "text": "A", "pdf_pages": [1]},
                        {
                            "kind": "text",
                            "text": "A complete provision.",
                            "pdf_pages": [1],
                        },
                    ],
                }
            ]
        }
    )

    report = audit_drafts(
        [("body", draft)],
        pages=[page],
        legal_start_pdf_page=1,
        legal_end_pdf_page=1,
    )

    assert not report.passed
    assert "duplicate_source_claim" in {issue.code for issue in report.issues}


def test_source_claim_audit_reports_an_extra_addressable_marker() -> None:
    page = {"pdf_page": 1, "text": "1. Example\nComplete wording."}
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "2.",
                    "heading": "Example",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "Complete wording.",
                            "pdf_pages": [1],
                        }
                    ],
                }
            ]
        }
    )

    report = audit_drafts(
        [("body", draft)],
        pages=[page],
        legal_start_pdf_page=1,
        legal_end_pdf_page=1,
    )

    assert "extra_marker" in {issue.code for issue in report.issues}
