"""What the cache holds for each Source, and what can be run against it next.

One row per acquired document, carried from acquisition to the corpus. Every
stage cell reports the same three things -- reached, blocked, or not yet -- so
the row reads as progress; what a stage actually produced goes in `detail`,
which is prose and belongs under the row rather than inside a cell.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STAGE_ORDER = ("classify", "extract", "structure", "candidate", "review", "promote")

REACHED = "done"
BLOCKED = "blocked"
PENDING = ""


@dataclass
class SourceState:
    digest: str
    title: str
    pages: int | None = None
    text_layer: str | None = None
    document_route: str | None = None
    stages: dict[str, str] = field(default_factory=dict)
    detail: dict[str, str] = field(default_factory=dict)
    next_stage: str | None = None
    blocked: str | None = None
    act_id: str | None = None
    candidate_name: str | None = None
    corpus_dir: Path | None = None

    def status(self, stage: str) -> str:
        return self.stages.get(stage, PENDING)

    @property
    def reached(self) -> str:
        done = [name for name in STAGE_ORDER if self.stages.get(name) == REACHED]
        return done[-1] if done else "acquire"

    @property
    def in_corpus(self) -> bool:
        return self.stages.get("promote") == REACHED

    @property
    def progress(self) -> int:
        return sum(1 for name in STAGE_ORDER if self.stages.get(name) == REACHED)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _newest(cache_root: Path, folder: str, digest: str) -> Path | None:
    directory = cache_root / folder
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(f"*-{digest}-*.json"))
    return matches[-1] if matches else None


def _candidates_by_digest(cache_root: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    directory = cache_root / "corpus-candidates"
    if not directory.is_dir():
        return found
    for entry in sorted(directory.iterdir()):
        manifest = _load(entry / "candidate.json")
        if manifest is None:
            continue
        digest = str(manifest.get("source_id", "")).removeprefix("sha256:")[:8]
        if digest:
            found[digest] = (entry.name, manifest)
    return found


def _fidelity_counts(candidate_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    matches = list(candidate_dir.glob("**/provisions.jsonl"))
    if not matches:
        return counts
    try:
        lines = matches[0].read_text(encoding="utf-8").splitlines()
    except OSError:
        return counts
    for line in lines:
        if not line.strip():
            continue
        try:
            fidelity = json.loads(line).get("text_fidelity", "")
        except json.JSONDecodeError:
            continue
        counts[fidelity] = counts.get(fidelity, 0) + 1
    return counts


def _corpus_by_digest(corpus_root: Path) -> dict[str, tuple[str, Path]]:
    """Promoted Acts keyed by the Source digest each one cites.

    An Act stays in the corpus after its candidate is cleared from the cache, so
    membership is read from the corpus rather than inferred from a candidate
    that may no longer exist.
    """
    promoted: dict[str, tuple[str, Path]] = {}
    if not corpus_root.is_dir():
        return promoted
    for path in sorted(corpus_root.glob("**/act.json")):
        act = _load(path)
        if act is None:
            continue
        for reference in act.get("source_refs") or []:
            digest = str(reference.get("source_id", "")).removeprefix("sha256:")[:8]
            if digest:
                promoted[digest] = (
                    act.get("act_id", ""),
                    path.parent.relative_to(corpus_root),
                )
    return promoted


def survey(cache_root: Path, corpus_root: Path | None = None) -> list[SourceState]:
    runs = cache_root / "runs"
    if not runs.is_dir():
        return []
    states: dict[str, SourceState] = {}
    for receipt in sorted(runs.glob("*.json"), reverse=True):
        payload = _load(receipt)
        if payload is None or payload.get("status") != "success":
            continue
        source = payload.get("source") or {}
        source_id = source.get("source_id", "")
        digest = source_id.removeprefix("sha256:")[:8]
        if not digest or digest in states:
            continue
        states[digest] = SourceState(
            digest=digest, title=source.get("document_title") or digest
        )

    candidates = _candidates_by_digest(cache_root)
    promoted = _corpus_by_digest(corpus_root) if corpus_root is not None else {}
    for digest, state in states.items():
        classification = _newest(cache_root, "classifications", digest)
        if classification is not None:
            report = _load(classification) or {}
            summary = report.get("summary") or {}
            state.pages = report.get("page_count")
            state.text_layer = summary.get("proposed_text_layer")
            state.document_route = summary.get("proposed_route")
            if state.document_route == "manual_review":
                state.stages["classify"] = BLOCKED
                state.blocked = "classifier routed this Source to manual review"
            else:
                state.stages["classify"] = REACHED
            state.detail["classify"] = f"route {state.document_route}"

        if _newest(cache_root, "extractions", digest) is not None:
            state.stages["extract"] = REACHED

        structure = _newest(cache_root, "structures", digest)
        if structure is not None:
            artifact = _load(structure) or {}
            summary = artifact.get("summary") or {}
            state.stages["structure"] = REACHED
            provisions = summary.get("provisions")
            findings = summary.get("review_findings")
            if provisions is not None and findings is not None:
                state.detail["structure"] = f"{provisions} provisions, {findings} findings"

        entry = candidates.get(digest)
        if entry is not None:
            name, manifest = entry
            state.candidate_name = name
            state.act_id = manifest.get("act_id")
            state.stages["candidate"] = REACHED
            counts = _fidelity_counts(cache_root / "corpus-candidates" / name)
            total = sum(counts.values())
            unreviewed = counts.get("machine_extracted", 0)
            if total:
                state.detail["candidate"] = f"{total} provisions"
                state.detail["review"] = f"{total - unreviewed} of {total} reviewed"
                if not unreviewed:
                    state.stages["review"] = REACHED
        if digest in promoted:
            act_id, directory = promoted[digest]
            state.act_id = state.act_id or act_id
            state.corpus_dir = directory
            # Reaching the corpus means every earlier stage happened, even where
            # the cache no longer holds the candidate that proves it.
            for name in STAGE_ORDER:
                state.stages.setdefault(name, REACHED)
                if state.stages[name] != BLOCKED:
                    state.stages[name] = REACHED

        state.next_stage = _next_stage(state)
    return list(states.values())


def _next_stage(state: SourceState) -> str | None:
    if state.blocked:
        return None
    for name in STAGE_ORDER:
        if state.stages.get(name) != REACHED:
            return name
    return None


def titles_by_digest(cache_root: Path) -> dict[str, str]:
    return {state.digest: state.title for state in survey(cache_root)}


def label_for(name: str, titles: dict[str, str]) -> str:
    """An artifact filename carries its Source digest between two hyphens."""
    for part in Path(name).stem.split("-"):
        if part in titles:
            return f"{titles[part]} - {name}"
    return name
