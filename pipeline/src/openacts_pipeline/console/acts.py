"""Building an Act record from a Source, so `candidate` stops needing hand JSON.

Sixteen fields are required to describe an Act, but the acquisition receipt
already carries most of them and the rest have one defensible default. Only the
identity of the Act -- what it is called, when it was passed, and whether the
text is as enacted -- is genuinely a human judgement, so that is all the form
asks for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from openacts_pipeline.common import PipelineError

TEXT_KINDS = ("as_enacted", "consolidated")
STATUSES = ("in_force", "repealed", "spent", "not_yet_commenced", "mixed", "unknown")
DATE_KEYS = ("assent", "publication", "commencement", "repeal")
DEFAULT_JURISDICTION = "ng-federal"


@dataclass(frozen=True)
class Draft:
    source_id: str
    digest: str
    official_title: str
    year: int | None
    number: str
    slug: str
    jurisdiction: str = DEFAULT_JURISDICTION
    text_kind: str = "as_enacted"
    status: str = "unknown"

    @property
    def act_id(self) -> str:
        return f"{self.jurisdiction}-act-{self.year}-{self.slug}"


def _year_in(title: str) -> int | None:
    years = re.findall(r"\b(1[89]\d{2}|20\d{2})\b", title)
    return int(years[-1]) if years else None


def slugify(value: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug


def _default_slug(title: str, number: str, year: int | None) -> str:
    """The corpus names an Act by its gazette number where one exists.

    `ng-federal-act-2023-37` is the Data Protection Act; falling back to a title
    slug keeps an Act without a number addressable.
    """
    if number.strip():
        return slugify(number)
    words = [
        word
        for word in slugify(title).split("-")
        if word not in {"act", "the", "of", "and", "a", str(year or "")}
    ]
    return "-".join(words[:4]) or "act"


def draft_from_receipt(receipt: dict[str, Any]) -> Draft:
    source = receipt.get("source") or {}
    title = str(source.get("document_title") or "").strip()
    year = _year_in(title)
    return Draft(
        source_id=str(source.get("source_id", "")),
        digest=str(source.get("source_id", "")).removeprefix("sha256:")[:8],
        official_title=title,
        year=year,
        number="",
        slug=_default_slug(title, "", year),
    )


def build(values: dict[str, str], source_id: str) -> dict[str, Any]:
    official = values.get("official_title", "").strip()
    slug = slugify(values.get("slug", ""))
    jurisdiction = values.get("jurisdiction", DEFAULT_JURISDICTION).strip()
    text_kind = values.get("text_kind", "as_enacted")
    status = values.get("status", "unknown")
    checked_through = values.get("checked_through_date", "").strip()
    number = values.get("number", "").strip()
    short = values.get("short_title", "").strip()

    if not official:
        raise PipelineError("invalid_act", "official title is required")
    if not slug:
        raise PipelineError("invalid_act", "slug is required")
    if text_kind not in TEXT_KINDS:
        raise PipelineError("invalid_act", f"text_kind must be one of {TEXT_KINDS}")
    if status not in STATUSES:
        raise PipelineError("invalid_act", f"status must be one of {STATUSES}")
    # `act.schema.json` requires both the date and a source for every status but
    # `unknown`, which is a state rather than a claim.
    if status != "unknown":
        try:
            date.fromisoformat(checked_through)
        except ValueError as error:
            raise PipelineError(
                "invalid_act",
                f"a status of {status} needs the date it was checked through, "
                "as YYYY-MM-DD",
            ) from error
    elif checked_through:
        raise PipelineError(
            "invalid_act",
            "a checked through date claims a status; leave it empty for unknown",
        )
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+", jurisdiction):
        raise PipelineError("invalid_act", "jurisdiction must look like ng-federal")
    try:
        year = int(values.get("year", ""))
    except ValueError as error:
        raise PipelineError("invalid_act", "year must be a number") from error

    return {
        "schema_version": "0.1.0",
        "record_type": "act",
        "act_id": f"{jurisdiction}-act-{year}-{slug}",
        "jurisdiction": jurisdiction,
        "country_code": jurisdiction.split("-")[0].upper(),
        "titles": {"official": official, "short": short or None, "long": None},
        "year": year,
        "number": number or None,
        "citation": official,
        "text_kind": text_kind,
        "dates": {
            key: {"date": None, "null_reason": "not_researched", "source_ids": []}
            for key in DATE_KEYS
        },
        "aliases": [alias for alias in (short,) if alias and alias != official],
        "status": status,
        "checked_through_date": checked_through or None,
        "status_source_ids": [source_id] if status != "unknown" else [],
        "source_refs": [
            {
                "source_id": source_id,
                "role": "authoritative_text",
                "scope_note": None,
            }
        ],
        "editorial_notes": [],
    }


def write(cache_root: Path, act: dict[str, Any]) -> str:
    name = f"{act['act_id']}.json"
    path = cache_root / "acts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(act, indent=2) + "\n", encoding="utf-8")
    return name
