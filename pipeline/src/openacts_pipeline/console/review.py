"""A candidate assembled for review: each Provision beside the findings against it.

The audit reports findings against the Source, not against the draft's own
identifiers, so nothing in the artifacts links a finding to the Provision a
reviewer would edit. Rebuilding that link here is the whole reason the review
page can show the disagreement instead of leaving a reviewer to search a
2487-line JSONL by hand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openacts_pipeline.structure_audit import _normalized

# Codes whose excerpt is the draft's own text (`claim.text`), so a quote match
# names the Provision to fix. `missing_source` reports text the draft never
# produced, so nothing in it can match a Provision -- only its page can.
DRAFT_QUOTED_CODES = ("unsupported_output", "duplicate_source_claim", "extra_marker")


@dataclass(frozen=True)
class Finding:
    kind: str
    detail: str
    pdf_page: int | None
    source_excerpt: str
    draft_excerpt: str = ""

    @property
    def is_variance(self) -> bool:
        return self.kind == "variance"

    @property
    def quote(self) -> str:
        """The draft text this finding is about, when it names any."""
        if self.is_variance:
            return self.draft_excerpt
        return self.source_excerpt if self.kind in DRAFT_QUOTED_CODES else ""


@dataclass
class ProvisionView:
    provision_id: str
    node_type: str
    display_label: str | None
    heading: str | None
    text: str
    has_table: bool
    pdf_pages: tuple[int, ...]
    text_fidelity: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.provision_id.split(":", 1)[-1]

    @property
    def label(self) -> str:
        return self.display_label or self.heading or self.short_id

    @property
    def reviewed(self) -> bool:
        return self.text_fidelity != "machine_extracted"


@dataclass(frozen=True)
class PageFindings:
    """Findings the audit could only place on a page, with that page's Provisions.

    Copying these onto every Provision sharing the page would flag 74 of 229 for
    nine findings, so they stay grouped and the Provisions are offered as leads.
    """

    pdf_page: int | None
    findings: list[Finding]
    provisions: list[ProvisionView]


@dataclass
class CandidateView:
    name: str
    act_id: str
    title: str
    provisions: list[ProvisionView]
    pages: list[PageFindings]
    audit_missing: bool
    source_characters: int = 0
    claimed_characters: int = 0

    @property
    def coverage(self) -> float | None:
        if not self.source_characters:
            return None
        return self.claimed_characters / self.source_characters

    @property
    def variance_count(self) -> int:
        return sum(
            1 for p in self.provisions for f in p.findings if f.is_variance
        )

    @property
    def attention(self) -> list[ProvisionView]:
        return [p for p in self.provisions if p.findings]

    @property
    def page_finding_count(self) -> int:
        return sum(len(page.findings) for page in self.pages)

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for provision in self.provisions:
            counts[provision.text_fidelity] = counts.get(provision.text_fidelity, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def remaining(self) -> int:
        return sum(1 for p in self.provisions if not p.reviewed)


def _cell_text(block: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for group in block.get("row_groups") or []:
        for row in group.get("rows") or []:
            for cell in row.get("cells") or []:
                for inner in cell.get("content_blocks") or []:
                    if inner.get("kind") == "text" and inner.get("text"):
                        parts.append(str(inner["text"]))
    return parts


def _text_of(provision: dict[str, Any]) -> tuple[str, bool]:
    parts: list[str] = []
    has_table = False
    for block in provision.get("content_blocks") or []:
        if block.get("kind") == "table":
            has_table = True
            parts.extend(_cell_text(block))
        elif block.get("text"):
            parts.append(str(block["text"]))
    return " ".join(parts), has_table


def _findings_from(audit: dict[str, Any]) -> list[Finding]:
    findings = [
        Finding(
            kind=issue.get("code", "issue"),
            detail=issue.get("message", ""),
            pdf_page=issue.get("pdf_page"),
            source_excerpt=issue.get("source_excerpt") or "",
        )
        for issue in audit.get("issues") or []
    ]
    findings += [
        Finding(
            kind="variance",
            detail=f"{variance.get('varying_characters', 0)} characters differ",
            pdf_page=variance.get("pdf_page"),
            source_excerpt=variance.get("source_excerpt") or "",
            draft_excerpt=variance.get("draft_excerpt") or "",
        )
        for variance in audit.get("variances") or []
    ]
    return findings


def _place(findings: list[Finding], views: list[ProvisionView]) -> list[PageFindings]:
    """Attach each finding to the Provision it quotes; group the rest by page.

    Returns the page groups, having filled in `ProvisionView.findings` as a side
    effect.
    """
    normalized = [(view, _normalized(view.text)) for view in views]
    by_page: dict[int | None, list[Finding]] = {}
    for finding in findings:
        quote = _normalized(finding.quote)
        holders = [view for view, text in normalized if quote and quote in text]
        if holders:
            for holder in holders:
                holder.findings.append(finding)
        else:
            by_page.setdefault(finding.pdf_page, []).append(finding)

    on_page: dict[int, list[ProvisionView]] = {}
    for view in views:
        for page in view.pdf_pages:
            on_page.setdefault(page, []).append(view)

    return [
        PageFindings(page, grouped, on_page.get(page, []) if page else [])
        for page, grouped in sorted(by_page.items(), key=lambda item: item[0] or 0)
    ]


def load(cache_root: Path, name: str) -> CandidateView:
    root = cache_root / "corpus-candidates" / name
    manifest = json.loads((root / "candidate.json").read_text(encoding="utf-8"))
    act_path = next(root.glob("**/act.json"))
    act = json.loads(act_path.read_text(encoding="utf-8"))

    records = (act_path.parent / "provisions.jsonl").read_text(encoding="utf-8")
    views: list[ProvisionView] = []
    for line in records.splitlines():
        if not line.strip():
            continue
        provision = json.loads(line)
        text, has_table = _text_of(provision)
        views.append(
            ProvisionView(
                provision_id=provision["provision_id"],
                node_type=provision["node_type"],
                display_label=provision.get("display_label"),
                heading=provision.get("heading"),
                text=text,
                has_table=has_table,
                pdf_pages=tuple(
                    sorted(
                        {
                            span["pdf_page"]
                            for span in provision.get("source_spans") or []
                            if span.get("pdf_page") is not None
                        }
                    )
                ),
                text_fidelity=provision["text_fidelity"],
            )
        )

    structure = manifest.get("input_structure", "")
    path = cache_root / structure if structure else None
    audit_missing = path is None or not path.exists()
    audit: dict[str, Any] = {}
    if not audit_missing:
        audit = json.loads(path.read_text(encoding="utf-8")).get("audit") or {}

    return CandidateView(
        name=name,
        act_id=act["act_id"],
        title=act["titles"]["official"],
        provisions=views,
        pages=_place(_findings_from(audit), views),
        audit_missing=audit_missing,
        source_characters=audit.get("source_characters", 0),
        claimed_characters=audit.get("claimed_characters", 0),
    )
