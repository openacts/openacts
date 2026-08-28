from openacts_pipeline.structure_audit import audit_drafts, matches_source_closely
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


def test_audit_excludes_post_operative_matter_below_the_terminator() -> None:
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "1.  The Commission shall protect personal information.\n"
                "I, certify, in accordance with section 2 (1) of the Acts\n"
                "Authentication Act, that this is a true copy of the Bill.\n"
                "EXPLANATORY MEMORANDUM\n"
                "This Act provides a legal framework.\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": None,
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "The Commission shall protect personal information.",
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)],
        pages=pages,
        legal_start_pdf_page=1,
        legal_end_pdf_page=1,
        legal_end_terminator="I, certify, in accordance with",
    )

    assert report.passed
    assert report.claimed_characters == report.source_characters
    reasons = {exclusion.reason for exclusion in report.exclusions}
    assert "post_operative_matter" in reasons
    assert report.excluded_characters > 0


def test_audit_without_a_terminator_still_demands_every_source_character() -> None:
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "1.  The Commission shall protect personal information.\n"
                "EXPLANATORY MEMORANDUM\n"
                "This Act provides a legal framework.\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": None,
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "The Commission shall protect personal information.",
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)],
        pages=pages,
        legal_start_pdf_page=1,
        legal_end_pdf_page=1,
    )

    assert not report.passed
    assert any(issue.code == "missing_source" for issue in report.issues)


def test_audit_matches_a_margin_heading_to_the_margin_not_the_body() -> None:
    """A gazette prints section headings in the margin, which pypdf appends after
    the body. The heading phrase also occurs inside the body sentence, so a
    first-fit matcher assigns the heading claim to the body copy and orphans both."""
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "15.  The National Commissioner shall be the Secretary to the Council,\n"
                "and —\n"
                "(a)  be responsible to the Council;\n"
                "Secretary to\n"
                "the Council\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "15.",
                    "heading": "Secretary to the Council",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": (
                                "The National Commissioner shall be the Secretary "
                                "to the Council, and —"
                            ),
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [
                        {
                            "node_type": "paragraph",
                            "display_label": "(a)",
                            "pdf_page": 1,
                            "content_blocks": [
                                {
                                    "kind": "text",
                                    "text": "be responsible to the Council;",
                                    "pdf_pages": [1],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)], pages=pages, legal_start_pdf_page=1, legal_end_pdf_page=1
    )

    assert report.claimed_characters == report.source_characters
    assert report.passed, [issue.model_dump() for issue in report.issues]


def test_audit_records_character_variance_instead_of_blocking() -> None:
    """Extraction turns 'or' into 'of'; the draft is right and must not block."""
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "13. Directives\n"
                "The Minister may give the Agency of the Director General such\n"
                "directives of a general nature as he may consider necessary.\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "13.",
                    "heading": "Directives",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": (
                                "The Minister may give the Agency or the Director "
                                "General such directives of a general nature as he "
                                "may consider necessary."
                            ),
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)], pages=pages, legal_start_pdf_page=1, legal_end_pdf_page=1
    )

    assert report.passed, [issue.model_dump() for issue in report.issues]
    assert len(report.variances) == 1
    variance = report.variances[0]
    assert variance.pdf_page == 1
    assert variance.varying_characters == 1
    assert report.varying_characters == 1


def test_audit_still_blocks_when_the_draft_omits_a_provision() -> None:
    """A dropped provision resembles nothing in the draft and must keep failing."""
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "1. Kept\nThe first provision is transcribed.\n"
                "2. Dropped\nA wholly different second provision nobody wrote down.\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Kept",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": "The first provision is transcribed.",
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)], pages=pages, legal_start_pdf_page=1, legal_end_pdf_page=1
    )

    assert not report.passed
    assert any(issue.code == "missing_source" for issue in report.issues)


def test_audit_excludes_unbracketed_margin_cross_references() -> None:
    """Gazettes print Cap. and Act No. pointers in the margin, without brackets."""
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "18. Pension\n"
                "Staff of the Agency shall be entitled to pension benefits.\n"
                "Act No. 4,\n"
                "2014\n"
                "Cap. P41,\n"
                "LFN, 2004\n"
                "Section 2(4)\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "18.",
                    "heading": "Pension",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": (
                                "Staff of the Agency shall be entitled to pension "
                                "benefits."
                            ),
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)], pages=pages, legal_start_pdf_page=1, legal_end_pdf_page=1
    )

    assert report.passed, [issue.model_dump() for issue in report.issues]
    reasons = {e.reason for e in report.exclusions}
    assert reasons == {"editorial_annotation"}


def test_audit_does_not_exclude_a_section_reference_inside_a_sentence() -> None:
    """Only a standalone line is furniture; the same words in a provision are law."""
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "5. Powers\n"
                "The Board may act under Section 2(4) of this Act as it sees fit.\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "5.",
                    "heading": "Powers",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": (
                                "The Board may act under Section 2(4) of this Act as "
                                "it sees fit."
                            ),
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)], pages=pages, legal_start_pdf_page=1, legal_end_pdf_page=1
    )

    assert report.passed, [issue.model_dump() for issue in report.issues]
    assert report.claimed_characters == report.source_characters


BODY = (
    "The Commission shall protect personal information, publish guidance for "
    "controllers and processors across the Federation, keep a register of every "
    "registered data controller of major importance, and report annually to the "
    "National Assembly on the discharge of its functions under this Act."
)


def test_a_report_with_outstanding_findings_is_still_reviewable() -> None:
    """Unclaimed source is a question for the reviewer, not a reason to fail."""
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "1. Duties\n"
                + BODY
                + "\nRights of a\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Duties",
                    "pdf_page": 1,
                    "content_blocks": [
                        {
                            "kind": "text",
                            "text": BODY,
                            "pdf_pages": [1],
                        }
                    ],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)], pages=pages, legal_start_pdf_page=1, legal_end_pdf_page=1
    )

    assert not report.passed
    assert report.reviewable
    assert report.source_coverage > 0.9


def test_a_report_that_accounts_for_almost_nothing_is_not_reviewable() -> None:
    """A broken structuring must not be handed to a reviewer as if it were work."""
    pages = [
        {
            "pdf_page": 1,
            "text": (
                "1. Duties\n"
                "The Commission shall protect personal information and publish "
                "guidance for controllers and processors across the Federation, "
                "and shall keep a register of every registered data controller.\n"
            ),
        }
    ]
    draft = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "section",
                    "display_label": "1.",
                    "heading": "Duties",
                    "pdf_page": 1,
                    "content_blocks": [],
                    "children": [],
                }
            ]
        }
    )

    report = audit_drafts(
        [(None, draft)], pages=pages, legal_start_pdf_page=1, legal_end_pdf_page=1
    )

    assert not report.passed
    assert not report.reviewable


def test_matches_source_closely_accepts_extraction_damage() -> None:
    """`or` read as `of` is the extraction being wrong, not the draft."""
    source = (
        "theministermaygivetheagencyofthedirectorgeneralsuchdirectivesofageneral"
        "natureashemayconsidernecessary"
    )
    draft = source.replace("agencyofthedirector", "agencyorthedirector")
    assert not draft in source
    assert matches_source_closely(draft, source)


def test_matches_source_closely_rejects_invented_wording() -> None:
    """Text the draft adds is invention and must still be caught."""
    source = (
        "thecommissionshallprotectpersonalinformationandpublishguidanceforcontrollers"
    )
    invented = source + "andmayimposeafineoftenmillionnaira"
    assert not matches_source_closely(invented, source)
