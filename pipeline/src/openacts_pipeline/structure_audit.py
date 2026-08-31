"""Deterministic completeness auditing for model-structured legal text."""

from __future__ import annotations

import bisect
import difflib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import pairwise
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
# Gazettes print cross-references in the margin without brackets, and the column
# is narrow enough to split one reference over several lines. Only a line that is
# wholly a reference counts; the same words inside a sentence are operative text.
EDITORIAL_REFERENCE_LINE = re.compile(
    r"(?ix)^\s*(?:"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+)?"
    r"schedules?(?:\s+section\s+\d+\s*(?:\([^)]*\)\s*)*)?"
    r"|acts?\s+no\.?\s*\d+\s*[,.]?"
    r"|cap\.?\s*[a-z]?\s*\d+\s*(?:lfn)?\s*[,.]?"
    r"|lfn\s*[,.]?\s*(?:\d{4})?\s*[,.]?"
    r"|section\s+\d+\s*(?:\([^)]*\)\s*)*[,.]?"
    r")\s*[,.]?\s*$"
)
# A bare year only continues a reference the previous line already began.
EDITORIAL_REFERENCE_CONTINUATION = re.compile(r"^\s*\d{4}\s*[,.]?\s*$")
PAREN_MARKER = re.compile(r"\((?:\d+[a-z]?|[a-z]+|[ivxlcdm]+)[ \t]*\)", re.IGNORECASE)

# Source the draft never claimed is a question for the reviewer, not a reason to
# throw the run away. A run that accounts for almost nothing is a different thing:
# that is a broken structuring, and no reviewer should be handed it.
MINIMUM_REVIEW_COVERAGE = 0.95

# A claim may differ from source only where extraction damaged it. Source-only
# text is an artifact the draft correctly left out - a margin note flattened into
# the sentence - so it is allowed generously. Text the draft adds is invention and
# stays tightly capped, which is what keeps the gate meaningful.
NEAR_MATCH_RATIO = 0.90
NEAR_MATCH_MAX_SOURCE_ONLY = 40
NEAR_MATCH_MAX_DRAFT_ONLY = 4
NEAR_MATCH_MIN_LENGTH = 40
# Furniture a sentence can straddle at a page break. Measured across the
# Electoral Act's 23 page-spanning blocks the largest was 172 characters.
MAX_PAGE_BREAK_GAP = 400
# Short words match anywhere in a page of prose, so only substantial ones
# carry evidence that a cell came from the source.
MIN_TABLE_CELL_TOKEN = 4


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

    reason: Literal[
        "printed_page_number",
        "recurring_header",
        "editorial_annotation",
        "post_operative_matter",
        "tabular_layout",
    ]
    pdf_page: int
    source_line: int
    source_excerpt: str
    normalized_characters: int = Field(ge=0)


class AuditVariance(BaseModel):
    """Source a claim matched except where extraction damaged it.

    Recorded for the reviewer rather than blocking completion, because the draft
    is usually right and the extraction wrong.
    """

    model_config = ConfigDict(extra="forbid")

    pdf_page: int
    source_line: int
    source_excerpt: str
    draft_excerpt: str
    varying_characters: int = Field(ge=0)


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
    varying_characters: int = Field(default=0, ge=0)
    issues: list[AuditIssue]
    exclusions: list[AuditExclusion] = Field(default_factory=list)
    variances: list[AuditVariance] = Field(default_factory=list)

    @property
    def source_coverage(self) -> float:
        if not self.source_characters:
            return 1.0
        return self.claimed_characters / self.source_characters

    @property
    def reviewable(self) -> bool:
        """Complete enough to hand a reviewer, even with findings outstanding."""
        return self.source_coverage >= MINIMUM_REVIEW_COVERAGE


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
    variances: list[AuditVariance]

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
            if len(signature) >= 8:
                candidates[signature] += 1
    # Alternating left/right Gazette headers form two recurring signatures.
    minimum = max(3, (len(pages) + 4) // 5)
    # A short line carries little evidence on its own, so it counts as furniture
    # only when it is printed on nearly every page, as a copyright notice is.
    short_minimum = max(3, (len(pages) * 4) // 5)
    return {
        line
        for line, count in candidates.items()
        if count >= (minimum if len(line) >= 20 else short_minimum)
    }


def _excluded_offsets(
    text: str,
    recurring_headers: set[str],
    pdf_page: int,
    terminator_offset: int | None = None,
) -> tuple[bytearray, list[AuditExclusion]]:
    excluded = bytearray(len(text))
    exclusions: list[AuditExclusion] = []
    ranges = _line_ranges(text)
    if terminator_offset is not None:
        excluded[terminator_offset:] = b"\x01" * (len(text) - terminator_offset)
        exclusions.append(
            AuditExclusion(
                reason="post_operative_matter",
                pdf_page=pdf_page,
                source_line=bisect.bisect_right(
                    [start for start, _, _ in ranges], terminator_offset
                ),
                source_excerpt=" ".join(text[terminator_offset:].split())[:240],
                normalized_characters=len(_normalized(text[terminator_offset:])),
            )
        )
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
    pages: list[dict[str, Any]],
    start_page: int,
    end_page: int,
    terminator: str | None = None,
) -> dict[int, _PageLedger]:
    scoped = [page for page in pages if start_page <= page["pdf_page"] <= end_page]
    headers = _recurring_headers(scoped)
    ledgers: dict[int, _PageLedger] = {}
    for page in scoped:
        raw_text = page["text"]
        terminator_offset = (
            raw_text.index(terminator)
            if terminator is not None
            and page["pdf_page"] == end_page
            and raw_text.count(terminator) == 1
            else None
        )
        excluded, exclusions = _excluded_offsets(
            raw_text, headers, page["pdf_page"], terminator_offset
        )
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
            variances=[],
        )
    return ledgers


def normalized_source_pages(pages: list[dict[str, Any]]) -> dict[int, str]:
    """Return audit-normalized page text with document furniture excluded."""
    if not pages:
        return {}
    ledgers = _build_ledgers(
        pages,
        min(page["pdf_page"] for page in pages),
        max(page["pdf_page"] for page in pages),
    )
    return {pdf_page: ledger.normalized for pdf_page, ledger in ledgers.items()}


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


def _claim_text(
    claim: _Claim, ledgers: dict[int, _PageLedger], *, allow_near: bool
) -> tuple[bool, bool]:
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
    if (
        allow_near
        and not duplicate
        and _claim_near_match(claim, needle, origins, joined, ledgers)
    ):
        return True, False
    return False, duplicate


def near_source_window(needle: str, haystack: str) -> tuple[int, str, int] | None:
    """Closest acceptable window of `haystack` for `needle`.

    Returns (start, window, varying characters), or None when the difference is
    too large to be extraction damage. Shared by the unit validator and the audit
    so both agree on what counts as close enough.
    """
    if len(needle) < NEAR_MATCH_MIN_LENGTH or not haystack:
        return None
    matcher = difflib.SequenceMatcher(None, needle, haystack, autojunk=False)
    anchor = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    if not anchor.size:
        return None
    start = max(0, anchor.b - anchor.a)
    # Source-only text lengthens the span the claim covers, so the right window
    # size is not known up front; score each candidate and keep the best.
    best: tuple[float, int, str] | None = None
    for slack in (0, 4, 8, 16, NEAR_MATCH_MAX_SOURCE_ONLY):
        window = haystack[start : start + len(needle) + slack]
        if not window:
            continue
        comparison = difflib.SequenceMatcher(None, window, needle, autojunk=False)
        source_only = 0
        draft_only = 0
        for tag, i1, i2, j1, j2 in comparison.get_opcodes():
            if tag == "equal":
                continue
            source_only += i2 - i1
            draft_only += j2 - j1
        if (
            comparison.ratio() < NEAR_MATCH_RATIO
            or source_only > NEAR_MATCH_MAX_SOURCE_ONLY
            or draft_only > NEAR_MATCH_MAX_DRAFT_ONLY
        ):
            continue
        # A one-character substitution reads as one varying character, not two.
        scored = (comparison.ratio(), -max(source_only, draft_only), window)
        if best is None or scored[:2] > best[:2]:
            best = scored
    if best is None:
        return None
    return start, best[2], -best[1]


def _longest_prefix_in(needle: str, haystack: str) -> int:
    """Length of the longest prefix of `needle` that occurs in `haystack`.

    Binary search is sound because a prefix of a substring is itself a
    substring, so presence is monotone in the prefix length.
    """
    low, high = 0, len(needle)
    while low < high:
        middle = (low + high + 1) // 2
        if needle[:middle] in haystack:
            low = middle
        else:
            high = middle - 1
    return low


def spans_page_break(needle: str, page_texts: list[str]) -> bool:
    """Whether `needle` runs contiguously through `page_texts`, in page order.

    Extraction interleaves marginal notes into the text flow, so a sentence
    continuing onto the next page is not a substring of those pages joined: the
    notes sit between its halves. It is still the source's own wording when each
    page contributes one contiguous run, in order, with only a bounded amount of
    furniture skipped at the seam.

    Taking the longest run each page can supply loses nothing, because a suffix
    of a substring is also a substring: if any split works, the greedy one does.
    """
    if not needle or len(page_texts) < 2:
        return False
    placements: list[tuple[int, int, int]] = []
    remaining = needle
    for index, page in enumerate(page_texts):
        final = index == len(page_texts) - 1
        take = len(remaining) if final else _longest_prefix_in(remaining, page)
        if take == 0:
            return False
        segment = remaining[:take]
        # A run leading into a break sits as late on its page as it can; one
        # continuing from a break starts as early as it can.
        start = page.find(segment) if index else page.rfind(segment)
        if start < 0:
            return False
        placements.append((len(page), start, take))
        remaining = remaining[take:]
    if remaining:
        return False
    return all(
        (length - (start + take)) + next_start <= MAX_PAGE_BREAK_GAP
        for (length, start, take), (_, next_start, _) in pairwise(placements)
    )


def matches_source_closely(needle: str, haystack: str) -> bool:
    """Whether normalized text differs from source only by extraction damage."""
    return needle in haystack or near_source_window(needle, haystack) is not None


def table_cell_tokens_present(text: str, source: str) -> bool:
    """Whether every substantial word of a table cell appears in the source.

    A multi-column table extracts column by column rather than in reading order,
    so a cell's wording is present but never contiguous. Tokens survive that
    reordering where a substring or windowed match cannot.
    """
    tokens = {
        normalized
        for token in re.findall(r"[^\W_]+", text)
        if len(normalized := _normalized(token)) >= MIN_TABLE_CELL_TOKEN
    }
    return bool(tokens) and all(token in source for token in tokens)


def _claim_near_match(
    claim: _Claim,
    needle: str,
    origins: list[tuple[int, int]],
    joined: str,
    ledgers: dict[int, _PageLedger],
) -> bool:
    found = near_source_window(needle, joined)
    if found is None:
        return False
    start, window, varying = found
    locations = origins[start : start + len(window)]
    # A neighbouring claim may already hold part of this span. Overlap is not a
    # reason to reject a close match, so claim only what is still unclaimed.
    outstanding = [
        (page, index)
        for page, index in locations
        if not ledgers[page].claimed[index]
    ]
    if not outstanding:
        return False
    for page, index in outstanding:
        ledgers[page].claimed[index] = 1
    page_number, first_index = outstanding[0]
    ledger = ledgers[page_number]
    raw_offset = ledger.raw_offsets[first_index]
    line = ledger.line_for_raw_offset(raw_offset)
    ledger.variances.append(
        AuditVariance(
            pdf_page=page_number,
            source_line=line,
            source_excerpt=ledger.line_text(line)[:240],
            draft_excerpt=" ".join(claim.text.split())[:240],
            varying_characters=varying,
        )
    )
    return True


def _audit(
    *,
    claims: Iterable[_Claim],
    pages: list[dict[str, Any]],
    legal_start_pdf_page: int,
    legal_end_pdf_page: int,
    legal_end_terminator: str | None = None,
) -> AuditReport:
    ledgers = _build_ledgers(
        pages, legal_start_pdf_page, legal_end_pdf_page, legal_end_terminator
    )
    issues: list[AuditIssue] = []
    # A claim's text can occur several times on one page: a margin heading also
    # appears inside the body sentence it labels. Taking the longest claims first
    # leaves each shorter claim the occurrence the longer ones did not take.
    ordered = sorted(
        claims, key=lambda claim: len(_normalized(claim.text)), reverse=True
    )
    tabular_text: defaultdict[int, list[str]] = defaultdict(list)
    for claim in ordered:
        if claim.description != "table cell":
            continue
        for pdf_page in claim.pdf_pages:
            tabular_text[pdf_page].append(_normalized(claim.text))
    # Every claim gets its exact span before any claim is allowed a near match,
    # so a fuzzy match cannot absorb source another provision still needs.
    deferred: list[tuple[_Claim, bool]] = []
    for claim in ordered:
        matched, duplicate = (
            _claim_marker(claim, ledgers)
            if claim.marker_label is not None
            else _claim_text(claim, ledgers, allow_near=False)
        )
        if not matched:
            deferred.append((claim, duplicate))
    for claim, exact_duplicate in deferred:
        duplicate = exact_duplicate
        if claim.marker_label is None:
            matched, duplicate = _claim_text(claim, ledgers, allow_near=True)
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
        previous_was_reference = False
        for line in sorted(unclaimed_by_line):
            indexes = unclaimed_by_line[line]
            text = ledger.line_text(line)
            # A margin cross-reference is furniture only where the draft did not
            # claim it. Judging that after claiming leaves a genuine Schedule
            # heading, which looks identical, safely in the hands of the draft.
            is_reference = bool(EDITORIAL_REFERENCE_LINE.match(text)) or (
                previous_was_reference
                and bool(EDITORIAL_REFERENCE_CONTINUATION.match(text))
            )
            previous_was_reference = is_reference
            if is_reference:
                for index in indexes:
                    ledger.claimed[index] = 1
                ledger.exclusions.append(
                    AuditExclusion(
                        reason="editorial_annotation",
                        pdf_page=ledger.pdf_page,
                        source_line=line,
                        source_excerpt=text[:240],
                        normalized_characters=len(indexes),
                    )
                )
                continue
            cells = tabular_text.get(ledger.pdf_page)
            if cells and table_cell_tokens_present(
                "".join(ledger.normalized[index] for index in indexes), " ".join(cells)
            ):
                for index in indexes:
                    ledger.claimed[index] = 1
                ledger.exclusions.append(
                    AuditExclusion(
                        reason="tabular_layout",
                        pdf_page=ledger.pdf_page,
                        source_line=line,
                        source_excerpt=text[:240],
                        normalized_characters=len(indexes),
                    )
                )
                continue
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
        varying_characters=sum(
            variance.varying_characters
            for ledger in ledgers.values()
            for variance in ledger.variances
        ),
        issues=issues,
        variances=[
            variance for ledger in ledgers.values() for variance in ledger.variances
        ],
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
    legal_end_terminator: str | None = None,
) -> AuditReport:
    return _audit(
        claims=_draft_claims(drafts),
        pages=pages,
        legal_start_pdf_page=legal_start_pdf_page,
        legal_end_pdf_page=legal_end_pdf_page,
        legal_end_terminator=legal_end_terminator,
    )


def audit_materialized_provisions(
    provisions: Iterable[dict[str, Any]],
    *,
    pages: list[dict[str, Any]],
    legal_start_pdf_page: int,
    legal_end_pdf_page: int,
    legal_end_terminator: str | None = None,
) -> AuditReport:
    return _audit(
        claims=_materialized_claims(provisions),
        pages=pages,
        legal_start_pdf_page=legal_start_pdf_page,
        legal_end_pdf_page=legal_end_pdf_page,
        legal_end_terminator=legal_end_terminator,
    )
