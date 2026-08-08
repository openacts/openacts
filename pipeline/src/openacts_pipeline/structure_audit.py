"""Deterministic completeness auditing for model-structured legal text."""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openacts_pipeline.structure_schema import (
    DraftContentBlock,
    DraftNode,
    StructureDraft,
    iter_block_texts,
)

MARKER_LABEL = re.compile(
    r"(?:\d+[a-z]?\.|\((?:\d+[a-z]?|[a-z]+|[ivxlcdm]+)\s*\))",
    re.IGNORECASE,
)
DECIMAL_MARKER = re.compile(r"(?m)^[ \t]*(\d+[a-z]?\.)", re.IGNORECASE)
PAREN_MARKER_RUN = re.compile(
    r"(?m)^[ \t]*(?:\d+[a-z]?\.[ \t]*(?:[—–-][ \t]*)?)?"
    r"((?:\((?:\d+[a-z]?|[a-z]+|[ivxlcdm]+)[ \t]*\)[ \t]*)+)",
    re.IGNORECASE,
)
PAREN_MARKER = re.compile(r"\((?:\d+[a-z]?|[a-z]+|[ivxlcdm]+)[ \t]*\)", re.IGNORECASE)


class AuditIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "missing_source",
        "unsupported_output",
        "duplicate_source_claim",
        "missing_marker",
        "extra_marker",
    ]
    message: str
    pdf_page: int | None = None
    source_line: int | None = None
    source_excerpt: str | None = None
    unit_id: str | None = None


class AuditExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["printed_page_number", "recurring_header", "editorial_annotation"]
    pdf_page: int
    source_line: int
    source_excerpt: str
    normalized_characters: int = Field(ge=0)


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    legal_start_pdf_page: int
    legal_end_pdf_page: int
    source_characters: int = Field(ge=0)
    claimed_characters: int = Field(ge=0)
    excluded_characters: int = Field(ge=0)
    source_markers: int = Field(ge=0)
    claimed_markers: int = Field(ge=0)
    issues: list[AuditIssue]
    exclusions: list[AuditExclusion] = Field(default_factory=list)


@dataclass(frozen=True)
class _Claim:
    text: str
    pdf_pages: tuple[int, ...]
    description: str
    unit_id: str | None
    marker_label: str | None = None


@dataclass(frozen=True)
class _Marker:
    label: str
    normalized_indexes: tuple[int, ...]


@dataclass
class _PageLedger:
    pdf_page: int
    raw_text: str
    normalized: str
    raw_offsets: list[int]
    line_starts: list[int]
    claimed: bytearray
    markers: list[_Marker]
    excluded_characters: int
    exclusions: list[AuditExclusion]

    def line_for_raw_offset(self, raw_offset: int) -> int:
        return bisect.bisect_right(self.line_starts, raw_offset)

    def line_text(self, line_number: int) -> str:
        lines = self.raw_text.splitlines()
        return lines[line_number - 1] if 0 < line_number <= len(lines) else ""


def _normalized(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value).casefold()
        if character.isalnum()
    )


def _line_ranges(text: str) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        ranges.append((offset, end, line.rstrip("\r\n")))
        offset = end
    if offset < len(text) or not ranges:
        ranges.append((offset, len(text), text[offset:]))
    return ranges


def _header_signature(line: str) -> str:
    compact = " ".join(line.split()).casefold()
    return re.sub(r"\d+", "#", compact)


def _recurring_headers(pages: list[dict[str, Any]]) -> set[str]:
    candidates: Counter[str] = Counter()
    for page in pages:
        for _, _, line in _line_ranges(page["text"])[:4]:
            signature = _header_signature(line)
            if len(signature) >= 20:
                candidates[signature] += 1
    # Alternating left/right Gazette headers form two recurring signatures.
    minimum = max(3, (len(pages) + 4) // 5)
    return {line for line, count in candidates.items() if count >= minimum}


def _excluded_offsets(
    text: str, recurring_headers: set[str], pdf_page: int
) -> tuple[bytearray, list[AuditExclusion]]:
    excluded = bytearray(len(text))
    exclusions: list[AuditExclusion] = []
    ranges = _line_ranges(text)
    first_nonblank = next(
        (index for index, (_, _, line) in enumerate(ranges) if line.strip()), None
    )
    for index, (start, end, line) in enumerate(ranges):
        reason: Literal["printed_page_number", "recurring_header"] | None = None
        if index == first_nonblank and line.strip().isdigit():
            reason = "printed_page_number"
        elif index < 4 and _header_signature(line) in recurring_headers:
            reason = "recurring_header"
        if reason is not None:
            excluded[start:end] = b"\x01" * (end - start)
            exclusions.append(
                AuditExclusion(
                    reason=reason,
                    pdf_page=pdf_page,
                    source_line=index + 1,
                    source_excerpt=line[:240],
                    normalized_characters=len(_normalized(line)),
                )
            )

    # Square brackets can also contain enacted dates, so exclude only annotations
    # whose wording identifies a structural cross-reference or alteration note.
    for match in re.finditer(r"\[[^\]]+\]", text, re.DOTALL):
        excerpt = match.group(0)
        compact = " ".join(excerpt[1:-1].split()).casefold()
        is_editorial = bool(
            re.match(
                r"^(?:sections?|parts?|items?|"
                r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|"
                r"ninth|tenth|\d+(?:st|nd|rd|th)?)\s+)?schedules?|"
                r"cap(?:\.|\b)|\d{4}\s+no\.?)",
                compact,
            )
            or "alteration act" in compact
            or any(
                phrase in compact
                for phrase in (
                    " is altered",
                    " is inserted",
                    " is substituted",
                    " is deleted",
                    " are altered",
                    " are inserted",
                    " are substituted",
                    " are deleted",
                )
            )
        )
        if not is_editorial:
            continue
        excluded[match.start() : match.end()] = b"\x01" * (match.end() - match.start())
        exclusions.append(
            AuditExclusion(
                reason="editorial_annotation",
                pdf_page=pdf_page,
                source_line=bisect.bisect_right(
                    [start for start, _, _ in ranges], match.start()
                ),
                source_excerpt=" ".join(excerpt.split())[:240],
                normalized_characters=len(_normalized(excerpt)),
            )
        )
    for index, (start, end, line) in enumerate(ranges):
        if line.strip().casefold() != "schedules":
            continue
        excluded[start:end] = b"\x01" * (end - start)
        exclusions.append(
            AuditExclusion(
                reason="editorial_annotation",
                pdf_page=pdf_page,
                source_line=index + 1,
                source_excerpt=line[:240],
                normalized_characters=len(_normalized(line)),
            )
        )
    return excluded, exclusions


def _source_markers(raw_text: str, raw_offsets: list[int]) -> list[_Marker]:
    raw_to_normalized: defaultdict[int, list[int]] = defaultdict(list)
    for normalized_index, raw_offset in enumerate(raw_offsets):
        raw_to_normalized[raw_offset].append(normalized_index)

    spans: list[tuple[str, int, int]] = []
    spans.extend(
        (match.group(1), match.start(1), match.end(1))
        for match in DECIMAL_MARKER.finditer(raw_text)
    )
    for run in PAREN_MARKER_RUN.finditer(raw_text):
        spans.extend(
            (match.group(0), run.start(1) + match.start(), run.start(1) + match.end())
            for match in PAREN_MARKER.finditer(run.group(1))
        )
    spans.sort(key=lambda item: item[1])
    markers: list[_Marker] = []
    for label, start, end in spans:
        indexes = tuple(
            index
            for raw_offset in range(start, end)
            for index in raw_to_normalized.get(raw_offset, ())
        )
        if indexes:
            markers.append(_Marker(_normalized(label), indexes))
    return markers


def _build_ledgers(
    pages: list[dict[str, Any]], start_page: int, end_page: int
) -> dict[int, _PageLedger]:
    scoped = [page for page in pages if start_page <= page["pdf_page"] <= end_page]
    headers = _recurring_headers(scoped)
    ledgers: dict[int, _PageLedger] = {}
    for page in scoped:
        raw_text = page["text"]
        excluded, exclusions = _excluded_offsets(raw_text, headers, page["pdf_page"])
        normalized_characters: list[str] = []
        raw_offsets: list[int] = []
        excluded_characters = 0
        for raw_offset, character in enumerate(raw_text):
            normalized_piece = unicodedata.normalize("NFKD", character).casefold()
            normalized_alnum = [part for part in normalized_piece if part.isalnum()]
            if excluded[raw_offset]:
                excluded_characters += len(normalized_alnum)
                continue
            normalized_characters.extend(normalized_alnum)
            raw_offsets.extend([raw_offset] * len(normalized_alnum))
        normalized = "".join(normalized_characters)
        ledgers[page["pdf_page"]] = _PageLedger(
            pdf_page=page["pdf_page"],
            raw_text=raw_text,
            normalized=normalized,
            raw_offsets=raw_offsets,
            line_starts=[start for start, _, _ in _line_ranges(raw_text)],
            claimed=bytearray(len(normalized)),
            markers=_source_markers(raw_text, raw_offsets),
            excluded_characters=excluded_characters,
            exclusions=exclusions,
        )
    return ledgers


def _draft_block_claims(
    block: DraftContentBlock, unit_id: str | None
) -> Iterator[_Claim]:
    for text, pdf_pages, description in iter_block_texts(block):
        marker = (
            _normalized(text)
            if description == "list label" and MARKER_LABEL.fullmatch(text.strip())
            else None
        )
        yield _Claim(text, tuple(pdf_pages), description, unit_id, marker)


def _draft_claims(
    drafts: Iterable[tuple[str | None, StructureDraft]],
) -> Iterator[_Claim]:
    def walk(node: DraftNode, unit_id: str | None) -> Iterator[_Claim]:
        if node.display_label:
            marker = (
                _normalized(node.display_label)
                if MARKER_LABEL.fullmatch(node.display_label.strip())
                else None
            )
            yield _Claim(
                node.display_label,
                (node.pdf_page,),
                "display label",
                unit_id,
                marker,
            )
        if node.heading:
            yield _Claim(node.heading, (node.pdf_page,), "heading", unit_id)
        for block in node.content_blocks:
            yield from _draft_block_claims(block, unit_id)
        for child in node.children:
            yield from walk(child, unit_id)

    for unit_id, draft in drafts:
        for node in draft.nodes:
            yield from walk(node, unit_id)


def _materialized_block_claims(
    block: dict[str, Any], unit_id: str | None
) -> Iterator[_Claim]:
    pages = tuple(span["pdf_page"] for span in block.get("source_spans", []))
    if block["kind"] in {"text", "quoted_text", "formula", "signature"}:
        yield _Claim(block["text"], pages, "content text", unit_id)
    elif block["kind"] == "list":
        for item in block["items"]:
            item_pages = tuple(span["pdf_page"] for span in item["source_spans"])
            if item.get("label"):
                label = item["label"]
                marker = (
                    _normalized(label)
                    if MARKER_LABEL.fullmatch(label.strip())
                    else None
                )
                yield _Claim(label, item_pages, "list label", unit_id, marker)
            for child in item["content_blocks"]:
                yield from _materialized_block_claims(child, unit_id)
    else:
        if block.get("caption"):
            caption = block["caption"]
            yield _Claim(
                caption["text"],
                tuple(span["pdf_page"] for span in caption["source_spans"]),
                "table caption",
                unit_id,
            )
        for group in block["row_groups"]:
            for row in group["rows"]:
                for cell in row["cells"]:
                    for child in cell["content_blocks"]:
                        yield from _materialized_block_claims(child, unit_id)


def _materialized_claims(provisions: Iterable[dict[str, Any]]) -> Iterator[_Claim]:
    for provision in provisions:
        pages = tuple(span["pdf_page"] for span in provision["source_spans"])
        for field, description in (
            ("display_label", "display label"),
            ("heading", "heading"),
        ):
            value = provision.get(field)
            if value:
                marker = (
                    _normalized(value)
                    if field == "display_label"
                    and MARKER_LABEL.fullmatch(value.strip())
                    else None
                )
                yield _Claim(value, pages, description, None, marker)
        for block in provision["content_blocks"]:
            yield from _materialized_block_claims(block, None)


def _claim_marker(claim: _Claim, ledgers: dict[int, _PageLedger]) -> tuple[bool, bool]:
    assert claim.marker_label is not None
    for page_number in claim.pdf_pages:
        ledger = ledgers.get(page_number)
        if ledger is None:
            continue
        candidates = [
            marker for marker in ledger.markers if marker.label == claim.marker_label
        ]
        for marker in candidates:
            if all(not ledger.claimed[index] for index in marker.normalized_indexes):
                for index in marker.normalized_indexes:
                    ledger.claimed[index] = 1
                return True, False
        if candidates:
            return False, True
    return False, False


def _claim_text(claim: _Claim, ledgers: dict[int, _PageLedger]) -> tuple[bool, bool]:
    needle = _normalized(claim.text)
    if not needle:
        return True, False
    origins: list[tuple[int, int]] = []
    joined = ""
    for page_number in claim.pdf_pages:
        ledger = ledgers.get(page_number)
        if ledger is None:
            continue
        joined += ledger.normalized
        origins.extend((page_number, index) for index in range(len(ledger.normalized)))
    duplicate = False
    start = 0
    while (position := joined.find(needle, start)) >= 0:
        locations = origins[position : position + len(needle)]
        if locations and all(
            not ledgers[page_number].claimed[index] for page_number, index in locations
        ):
            for page_number, index in locations:
                ledgers[page_number].claimed[index] = 1
            return True, False
        duplicate = True
        start = position + 1
    return False, duplicate


def _audit(
    *,
    claims: Iterable[_Claim],
    pages: list[dict[str, Any]],
    legal_start_pdf_page: int,
    legal_end_pdf_page: int,
) -> AuditReport:
    ledgers = _build_ledgers(pages, legal_start_pdf_page, legal_end_pdf_page)
    issues: list[AuditIssue] = []
    for claim in claims:
        matched, duplicate = (
            _claim_marker(claim, ledgers)
            if claim.marker_label is not None
            else _claim_text(claim, ledgers)
        )
        if matched:
            continue
        page = claim.pdf_pages[0] if claim.pdf_pages else None
        issue_code = (
            "duplicate_source_claim"
            if duplicate
            else "extra_marker"
            if claim.marker_label is not None
            else "unsupported_output"
        )
        issues.append(
            AuditIssue(
                code=issue_code,
                message=(
                    f"{claim.description} is claimed more than once"
                    if duplicate
                    else f"source has no matching marker for {claim.description}"
                    if claim.marker_label is not None
                    else f"{claim.description} is not an exact normalized source span"
                ),
                pdf_page=page,
                source_excerpt=claim.text[:240],
                unit_id=claim.unit_id,
            )
        )

    source_markers = 0
    claimed_markers = 0
    for ledger in ledgers.values():
        for marker in ledger.markers:
            source_markers += 1
            if all(ledger.claimed[index] for index in marker.normalized_indexes):
                claimed_markers += 1
            else:
                raw_offset = ledger.raw_offsets[marker.normalized_indexes[0]]
                line = ledger.line_for_raw_offset(raw_offset)
                issues.append(
                    AuditIssue(
                        code="missing_marker",
                        message=f"source marker is not represented: {marker.label}",
                        pdf_page=ledger.pdf_page,
                        source_line=line,
                        source_excerpt=ledger.line_text(line)[:240],
                    )
                )

    for ledger in ledgers.values():
        unclaimed_by_line: defaultdict[int, list[int]] = defaultdict(list)
        for index, claimed in enumerate(ledger.claimed):
            if not claimed:
                unclaimed_by_line[
                    ledger.line_for_raw_offset(ledger.raw_offsets[index])
                ].append(index)
        for line, indexes in unclaimed_by_line.items():
            issues.append(
                AuditIssue(
                    code="missing_source",
                    message=(
                        f"{len(indexes)} normalized source characters are unclaimed"
                    ),
                    pdf_page=ledger.pdf_page,
                    source_line=line,
                    source_excerpt=ledger.line_text(line)[:240],
                )
            )

    source_characters = sum(len(ledger.normalized) for ledger in ledgers.values())
    claimed_characters = sum(sum(ledger.claimed) for ledger in ledgers.values())
    return AuditReport(
        passed=not issues,
        legal_start_pdf_page=legal_start_pdf_page,
        legal_end_pdf_page=legal_end_pdf_page,
        source_characters=source_characters,
        claimed_characters=claimed_characters,
        excluded_characters=sum(
            ledger.excluded_characters for ledger in ledgers.values()
        ),
        source_markers=source_markers,
        claimed_markers=claimed_markers,
        issues=issues,
        exclusions=[
            exclusion for ledger in ledgers.values() for exclusion in ledger.exclusions
        ],
    )


def audit_drafts(
    drafts: Iterable[tuple[str | None, StructureDraft]],
    *,
    pages: list[dict[str, Any]],
    legal_start_pdf_page: int,
    legal_end_pdf_page: int,
) -> AuditReport:
    return _audit(
        claims=_draft_claims(drafts),
        pages=pages,
        legal_start_pdf_page=legal_start_pdf_page,
        legal_end_pdf_page=legal_end_pdf_page,
    )


def audit_materialized_provisions(
    provisions: Iterable[dict[str, Any]],
    *,
    pages: list[dict[str, Any]],
    legal_start_pdf_page: int,
    legal_end_pdf_page: int,
) -> AuditReport:
    return _audit(
        claims=_materialized_claims(provisions),
        pages=pages,
        legal_start_pdf_page=legal_start_pdf_page,
        legal_end_pdf_page=legal_end_pdf_page,
    )
