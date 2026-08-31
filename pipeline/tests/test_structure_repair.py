import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.structure_audit import AuditIssue, AuditReport
from openacts_pipeline.structure_repair import apply_repair_patch, repair_improves
from openacts_pipeline.structure_schema import (
    DraftTextBlock,
    RepairPatch,
    StructureDraft,
)


def _report(*issues: AuditIssue, claimed: int) -> AuditReport:
    return AuditReport(
        passed=not issues,
        legal_start_pdf_page=1,
        legal_end_pdf_page=2,
        source_characters=100,
        claimed_characters=claimed,
        excluded_characters=0,
        source_markers=2,
        claimed_markers=2,
        issues=list(issues),
    )


def test_repair_patch_is_copy_on_write_and_only_accepts_audited_improvement() -> None:
    original = StructureDraft.model_validate(
        {
            "nodes": [
                {
                    "node_type": "chapter",
                    "display_label": "CHAPTER V",
                    "pdf_page": 1,
                    "children": [
                        {
                            "node_type": "subsection",
                            "display_label": "(1)",
                            "pdf_page": 1,
                            "content_blocks": [
                                {
                                    "kind": "text",
                                    "text": "Main wording.",
                                    "pdf_pages": [1],
                                }
                            ],
                        },
                        {
                            "node_type": "paragraph",
                            "pdf_page": 2,
                            "content_blocks": [
                                {
                                    "kind": "text",
                                    "text": "Provided that the qualification remains.",
                                    "pdf_pages": [2],
                                }
                            ],
                        },
                    ],
                }
            ]
        }
    )
    patch = RepairPatch.model_validate(
        {
            "unit_id": "chapter-05",
            "operations": [
                {
                    "op": "move",
                    "from_path": "/nodes/0/children/1/content_blocks/0",
                    "path": "/nodes/0/children/0/content_blocks/-",
                },
                {"op": "remove", "path": "/nodes/0/children/1"},
            ],
        }
    )

    repaired = apply_repair_patch(original, patch)

    assert len(original.nodes[0].children) == 2
    assert len(repaired.nodes[0].children) == 1
    moved = repaired.nodes[0].children[0].content_blocks[-1]
    assert isinstance(moved, DraftTextBlock)
    assert moved.text.startswith("Provided that")
    previous = _report(
        AuditIssue(
            code="unsupported_output",
            message="invalid proviso",
            pdf_page=2,
            unit_id="chapter-05",
        ),
        claimed=60,
    )
    improved = _report(claimed=100)
    outside_regression = _report(
        AuditIssue(
            code="missing_source",
            message="unrelated omission",
            pdf_page=1,
            unit_id="chapter-04",
        ),
        claimed=90,
    )
    regressive_tradeoff = _report(
        AuditIssue(
            code="missing_source",
            message="larger local omission",
            pdf_page=2,
            unit_id="chapter-05",
        ),
        claimed=10,
    )
    assert repair_improves(previous, improved, "chapter-05")
    assert not repair_improves(previous, outside_regression, "chapter-05")
    assert not repair_improves(previous, regressive_tradeoff, "chapter-05")

    invalid_path = RepairPatch.model_validate(
        {
            "unit_id": "chapter-05",
            "operations": [
                {
                    "op": "replace",
                    "path": "/nodes/0/children/99/pdf_page",
                    "value": 2,
                }
            ],
        }
    )
    with pytest.raises(PipelineError, match="children/99.*2 item"):
        apply_repair_patch(original, invalid_path)


def test_critic_sees_only_the_units_holding_the_issue_mass() -> None:
    from openacts_pipeline.structure_runtime import _units_by_issue_mass

    def issues(unit_id: str, count: int) -> list[AuditIssue]:
        return [
            AuditIssue(
                code="missing_source",
                message="unclaimed",
                pdf_page=1,
                unit_id=unit_id,
            )
            for _ in range(count)
        ]

    report = _report(
        *issues("chapter-08", 1062),
        *issues("chapter-09", 805),
        *issues("schedule-10", 319),
        *[issue for index in range(29) for issue in issues(f"healthy-{index:02d}", 6)],
        claimed=50,
    )

    assert _units_by_issue_mass(report) == ["chapter-08", "chapter-09", "schedule-10"]


def test_issue_mass_selection_stays_bounded_when_issues_are_spread_evenly() -> None:
    from openacts_pipeline.structure_runtime import _units_by_issue_mass

    report = _report(
        *[
            AuditIssue(
                code="missing_source",
                message="unclaimed",
                pdf_page=1,
                unit_id=f"unit-{index:02d}",
            )
            for index in range(40)
        ],
        claimed=50,
    )

    assert len(_units_by_issue_mass(report)) == 8
