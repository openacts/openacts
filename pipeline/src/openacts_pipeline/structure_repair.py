"""Fail-closed surgical edits for model-produced structure drafts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from openacts_pipeline.common import PipelineError
from openacts_pipeline.structure_audit import AuditIssue, AuditReport
from openacts_pipeline.structure_schema import RepairPatch, StructureDraft


def _tokens(path: str) -> list[str]:
    tokens = []
    for raw in path.removeprefix("/").split("/"):
        index = 0
        while (index := raw.find("~", index)) >= 0:
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise PipelineError(
                    "invalid_repair_patch", f"invalid JSON pointer escape in {path}"
                )
            index += 2
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tokens


def _list_index(token: str, length: int, *, allow_end: bool, path: str) -> int:
    if token == "-" and allow_end:
        return length
    if not token.isdigit():
        raise PipelineError(
            "invalid_repair_patch",
            f"path {path!r} has invalid list index {token!r}",
        )
    index = int(token)
    upper = length if allow_end else length - 1
    if index > upper:
        raise PipelineError(
            "invalid_repair_patch",
            f"path {path!r} uses list index {index}, but that list has "
            f"{length} item(s)",
        )
    return index


def _parent(document: Any, path: str) -> tuple[Any, str]:
    tokens = _tokens(path)
    if len(tokens) < 2 or tokens[0] != "nodes":
        raise PipelineError(
            "invalid_repair_patch", f"path {path!r} is outside the draft"
        )
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise PipelineError(
                    "invalid_repair_patch", f"path {path!r} does not exist"
                )
            current = current[token]
        elif isinstance(current, list):
            current = current[
                _list_index(token, len(current), allow_end=False, path=path)
            ]
        else:
            raise PipelineError(
                "invalid_repair_patch", f"path {path!r} crosses a scalar value"
            )
    return current, tokens[-1]


def _remove(document: Any, path: str) -> Any:
    parent, token = _parent(document, path)
    if isinstance(parent, dict):
        if token not in parent:
            raise PipelineError("invalid_repair_patch", f"path {path!r} does not exist")
        return parent.pop(token)
    if isinstance(parent, list):
        return parent.pop(_list_index(token, len(parent), allow_end=False, path=path))
    raise PipelineError(
        "invalid_repair_patch", f"path {path!r} does not reference a container"
    )


def _add(document: Any, path: str, value: Any) -> None:
    parent, token = _parent(document, path)
    if isinstance(parent, dict):
        parent[token] = value
        return
    if isinstance(parent, list):
        parent.insert(_list_index(token, len(parent), allow_end=True, path=path), value)
        return
    raise PipelineError(
        "invalid_repair_patch", f"path {path!r} does not reference a container"
    )


def _replace(document: Any, path: str, value: Any) -> None:
    parent, token = _parent(document, path)
    if isinstance(parent, dict):
        if token not in parent:
            raise PipelineError("invalid_repair_patch", f"path {path!r} does not exist")
        parent[token] = value
        return
    if isinstance(parent, list):
        parent[_list_index(token, len(parent), allow_end=False, path=path)] = value
        return
    raise PipelineError(
        "invalid_repair_patch", f"path {path!r} does not reference a container"
    )


def apply_repair_patch(draft: StructureDraft, patch: RepairPatch) -> StructureDraft:
    document = draft.model_dump(mode="json")
    for operation in patch.operations:
        if operation.op == "add":
            _add(document, operation.path, operation.value)
        elif operation.op == "remove":
            _remove(document, operation.path)
        elif operation.op == "replace":
            _replace(document, operation.path, operation.value)
        else:
            assert operation.from_path is not None
            if operation.path == operation.from_path or operation.path.startswith(
                operation.from_path.rstrip("/") + "/"
            ):
                raise PipelineError(
                    "invalid_repair_patch",
                    "move destination cannot be the source or its descendant",
                )
            value = _remove(document, operation.from_path)
            _add(document, operation.path, value)
    try:
        return StructureDraft.model_validate(document)
    except ValidationError as exc:
        raise PipelineError(
            "invalid_repair_patch", f"patched draft is not schema-valid: {exc}"
        ) from exc


def repair_score(report: AuditReport) -> tuple[int, int, int, int]:
    hard_issues = sum(
        issue.code in {"unsupported_output", "duplicate_source_claim", "extra_marker"}
        for issue in report.issues
    )
    return (
        hard_issues,
        max(0, report.source_markers - report.claimed_markers),
        max(0, report.source_characters - report.claimed_characters),
        len(report.issues),
    )


def _issue_fingerprint(issue: AuditIssue) -> tuple[Any, ...]:
    return (
        issue.code,
        issue.message,
        issue.pdf_page,
        issue.source_line,
        issue.source_excerpt,
        issue.unit_id,
    )


def repair_improves(previous: AuditReport, trial: AuditReport, unit_id: str) -> bool:
    previous_outside = Counter(
        _issue_fingerprint(issue)
        for issue in previous.issues
        if issue.unit_id != unit_id
    )
    trial_outside = Counter(
        _issue_fingerprint(issue) for issue in trial.issues if issue.unit_id != unit_id
    )
    previous_score = repair_score(previous)
    trial_score = repair_score(trial)
    return (
        not (trial_outside - previous_outside)
        and all(
            trial_value <= previous_value
            for trial_value, previous_value in zip(
                trial_score, previous_score, strict=True
            )
        )
        and trial_score != previous_score
    )
