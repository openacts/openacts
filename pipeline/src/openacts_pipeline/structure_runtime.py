"""Agent runtime and resumable graph for legal-document structuring."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext, StepContext

from openacts_pipeline.common import PipelineError, write_json_result
from openacts_pipeline.config import StructureSettings
from openacts_pipeline.structure_audit import AuditIssue, AuditReport, audit_drafts
from openacts_pipeline.structure_repair import (
    apply_repair_patch,
    repair_improves,
    repair_score,
)
from openacts_pipeline.structure_schema import (
    RepairPatch,
    RepairPlan,
    StructureDraft,
    StructurePlan,
    StructureUnit,
)


@dataclass(frozen=True)
class ModelRun:
    output: BaseModel
    model: str
    output_mode: str
    latency_seconds: float
    usage: dict[str, int]


class RejectedModelOutput(PipelineError):
    """A schema-valid model response rejected by deterministic validation."""

    def __init__(
        self,
        model: str,
        output: BaseModel,
        validation_error: PipelineError,
    ) -> None:
        super().__init__(
            "model_invalid_output",
            f"{model} returned a source-invalid structured draft: "
            f"{validation_error.code}: {validation_error}",
            retryable=True,
        )
        self.output = output
        self.validation_error = validation_error


class RejectedCheckpoint(PipelineError):
    """A preserved model response that the repair graph may replace."""

    def __init__(
        self,
        *,
        output: BaseModel,
        validation_error: PipelineError,
        relative_path: Path,
        checkpoint_reused: bool,
    ) -> None:
        super().__init__(
            "model_invalid_output",
            f"{validation_error}; rejected draft: {relative_path.as_posix()}",
            retryable=True,
        )
        self.output = output
        self.validation_error = validation_error
        self.relative_path = relative_path
        self.checkpoint_reused = checkpoint_reused


ModelRunner = Callable[
    [
        str,
        StructureSettings,
        str,
        type[BaseModel],
        Callable[[BaseModel], None] | None,
    ],
    Awaitable[ModelRun],
]
ProgressReporter = Callable[[dict[str, Any]], None]


def _checkpoint_attempts(
    *,
    cache_root: Path,
    work_key: str,
    pass_index: int,
    pass_name: str,
) -> range | tuple[int]:
    directory = cache_root / "structure-work" / work_key

    def exists(name: str, *, include_failure: bool) -> bool:
        success = directory / f"{pass_index:03d}-{name}.json"
        failure = directory / f"{pass_index:03d}-{name}.failure.json"
        return success.exists() or (include_failure and failure.exists())

    if exists(pass_name, include_failure=False):
        return (0,)
    if exists(f"{pass_name}-retry-1", include_failure=True):
        return (1,)
    return range(2)


async def run_checkpointed(
    *,
    cache_root: Path,
    work_key: str,
    pass_index: int,
    pass_name: str,
    source_id: str,
    raw_text: str,
    settings: StructureSettings,
    scope: str,
    output_type: type[BaseModel],
    runner: ModelRunner,
    validate: Callable[[BaseModel], None],
    structure_version: int,
    prompt_version: int,
) -> tuple[ModelRun, bool]:
    relative_path = (
        Path("structure-work") / work_key / f"{pass_index:03d}-{pass_name}.json"
    )
    path = cache_root / relative_path
    failure_relative_path = relative_path.with_name(
        f"{pass_index:03d}-{pass_name}.failure.json"
    )
    scope_hash = hashlib.sha256(scope.encode()).hexdigest()
    input_hash = hashlib.sha256(raw_text.encode()).hexdigest()

    async def run_scoped_checkpoint() -> tuple[ModelRun, bool]:
        resume_material = "|".join(
            (
                source_id,
                input_hash,
                scope_hash,
                settings.primary_model,
                str(structure_version),
                str(prompt_version),
                output_type.__name__,
            )
        )
        digest = hashlib.sha256(resume_material.encode()).hexdigest()[:12]
        suffix = f"-scope-{digest}"
        if pass_name.endswith(suffix):
            raise PipelineError(
                "invalid_structure_checkpoint",
                f"scoped {pass_name} checkpoint metadata is inconsistent",
            )
        return await run_checkpointed(
            cache_root=cache_root,
            work_key=work_key,
            pass_index=pass_index,
            pass_name=f"{pass_name}{suffix}",
            source_id=source_id,
            raw_text=raw_text,
            settings=settings,
            scope=scope,
            output_type=output_type,
            runner=runner,
            validate=validate,
            structure_version=structure_version,
            prompt_version=prompt_version,
        )

    if path.exists():
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            if (
                checkpoint["stage"] != "structure_pass"
                or checkpoint["structure_version"] != structure_version
                or checkpoint["prompt_version"] != prompt_version
                or checkpoint["source_id"] != source_id
                or checkpoint["configured_model"] != settings.primary_model
                or checkpoint["pass_index"] != pass_index
                or checkpoint["pass_name"] != pass_name
                or checkpoint["output_type"] != output_type.__name__
            ):
                raise ValueError("checkpoint identity does not match this pass")
            stale_scope = (
                checkpoint["scope_sha256"] != scope_hash
                or checkpoint["input_sha256"] != input_hash
            )
            output = output_type.model_validate(checkpoint["output"])
            run_data = checkpoint["model_run"]
            run = ModelRun(
                output=output,
                model=run_data["model"],
                output_mode=run_data["output_mode"],
                latency_seconds=run_data["latency_seconds"],
                usage=run_data["usage"],
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PipelineError(
                "invalid_structure_checkpoint", f"cannot resume {pass_name}: {exc}"
            ) from exc
        if stale_scope:
            try:
                validate(output)
            except PipelineError:
                return await run_scoped_checkpoint()
            return (
                ModelRun(
                    output=output,
                    model=run.model,
                    output_mode="revalidated_stale_checkpoint",
                    latency_seconds=run.latency_seconds,
                    usage=run.usage,
                ),
                True,
            )
        try:
            validate(output)
        except PipelineError as exc:
            raise RejectedCheckpoint(
                output=output,
                validation_error=exc,
                relative_path=relative_path,
                checkpoint_reused=True,
            ) from exc
        return run, True

    failure_path = cache_root / failure_relative_path
    if failure_path.exists():
        try:
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            if (
                failure["stage"] != "structure_pass"
                or failure["status"] != "failure"
                or failure["structure_version"] != structure_version
                or failure["prompt_version"] != prompt_version
                or failure["source_id"] != source_id
                or failure["configured_model"] != settings.primary_model
                or failure["pass_index"] != pass_index
                or failure["pass_name"] != pass_name
                or failure["output_type"] != output_type.__name__
            ):
                raise ValueError("failure checkpoint identity does not match this pass")
            stale_scope = (
                failure["scope_sha256"] != scope_hash
                or failure["input_sha256"] != input_hash
            )
            output = output_type.model_validate(failure["output"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PipelineError(
                "invalid_structure_checkpoint",
                f"cannot resume failed {pass_name}: {exc}",
            ) from exc
        if stale_scope:
            try:
                validate(output)
            except PipelineError:
                return await run_scoped_checkpoint()
            return (
                ModelRun(
                    output=output,
                    model=settings.primary_model,
                    output_mode="revalidated_stale_checkpoint",
                    latency_seconds=0.0,
                    usage={"requests": 0, "input_tokens": 0, "output_tokens": 0},
                ),
                True,
            )
        try:
            validate(output)
        except PipelineError as exc:
            raise RejectedCheckpoint(
                output=output,
                validation_error=exc,
                relative_path=failure_relative_path,
                checkpoint_reused=True,
            ) from exc
        return (
            ModelRun(
                output=output,
                model=settings.primary_model,
                output_mode="revalidated_failure_checkpoint",
                latency_seconds=0.0,
                usage={"requests": 0, "input_tokens": 0, "output_tokens": 0},
            ),
            True,
        )

    try:
        run = await runner(raw_text, settings, scope, output_type, validate)
    except RejectedModelOutput as exc:
        failure = {
            "stage": "structure_pass",
            "status": "failure",
            "structure_version": structure_version,
            "prompt_version": prompt_version,
            "source_id": source_id,
            "configured_model": settings.primary_model,
            "pass_index": pass_index,
            "pass_name": pass_name,
            "scope_sha256": scope_hash,
            "input_sha256": input_hash,
            "input_characters": len(raw_text),
            "output_type": output_type.__name__,
            "error": exc.as_dict(),
            "validation_error": exc.validation_error.as_dict(),
            "output": exc.output.model_dump(mode="json"),
        }
        write_json_result(cache_root, failure, failure_relative_path)
        raise RejectedCheckpoint(
            output=exc.output,
            validation_error=exc.validation_error,
            relative_path=failure_relative_path,
            checkpoint_reused=False,
        ) from exc
    if not isinstance(run.output, output_type):
        raise PipelineError(
            "model_invalid_output", f"{pass_name} used the wrong schema"
        )
    validate(run.output)
    checkpoint = {
        "stage": "structure_pass",
        "status": "success",
        "structure_version": structure_version,
        "prompt_version": prompt_version,
        "source_id": source_id,
        "configured_model": settings.primary_model,
        "pass_index": pass_index,
        "pass_name": pass_name,
        "scope_sha256": scope_hash,
        "input_sha256": input_hash,
        "input_characters": len(raw_text),
        "output_type": output_type.__name__,
        "model_run": {
            "model": run.model,
            "output_mode": run.output_mode,
            "latency_seconds": run.latency_seconds,
            "usage": run.usage,
        },
        "output": run.output.model_dump(mode="json"),
    }
    write_json_result(cache_root, checkpoint, relative_path)
    return run, False


@dataclass
class UnitResult:
    unit: StructureUnit
    draft: StructureDraft
    run: ModelRun
    pass_name: str
    checkpoint_reused: bool
    validation_issue: AuditIssue | None = None


@dataclass
class WorkflowResult:
    plan: StructurePlan
    drafts: list[StructureDraft]
    audit: AuditReport
    pass_records: list[dict[str, Any]]
    repair_rounds: int


PlanValidator = Callable[[StructurePlan], None]
UnitValidator = Callable[[StructureDraft, StructureUnit], None]
PlanScopeBuilder = Callable[[StructurePlan | None, AuditReport | None], str]
UnitScopeBuilder = Callable[
    [StructureUnit, list[AuditIssue], StructureDraft | None], str
]
RepairScopeBuilder = Callable[[StructureUnit, list[AuditIssue], StructureDraft], str]
PagesTextBuilder = Callable[[int, int], str]


@dataclass
class StructureDeps:
    cache_root: Path
    work_key: str
    source_id: str
    pages: list[dict[str, Any]]
    page_count: int
    settings: StructureSettings
    runner: ModelRunner
    plan_scope: PlanScopeBuilder
    unit_scope: UnitScopeBuilder
    repair_scope: RepairScopeBuilder
    pages_text: PagesTextBuilder
    validate_plan: PlanValidator
    validate_unit: UnitValidator
    structure_version: int
    prompt_version: int
    progress: ProgressReporter | None = None


@dataclass
class StructureState:
    plan: StructurePlan | None = None
    unit_results: dict[str, UnitResult] = field(default_factory=dict)
    audit: AuditReport | None = None
    pass_records: list[dict[str, Any]] = field(default_factory=list)
    repair_round: int = 0
    plan_revision: int = 0


def _emit(deps: StructureDeps, event: str, **metadata: Any) -> None:
    if deps.progress is not None:
        deps.progress({"event": event, **metadata})


def _pass_record(
    pass_name: str,
    run: ModelRun,
    reused: bool,
    *,
    unit: StructureUnit | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "pass": pass_name,
        "model": run.model,
        "output_mode": run.output_mode,
        "checkpoint_reused": reused,
        "latency_seconds": run.latency_seconds,
        "usage": run.usage,
    }
    if unit is not None:
        record["unit"] = unit.model_dump(mode="json")
    return record


def _enforce_token_budget(state: StructureState, deps: StructureDeps) -> None:
    used = sum(
        record["usage"].get("input_tokens", 0) + record["usage"].get("output_tokens", 0)
        for record in state.pass_records
        if not record["checkpoint_reused"]
    )
    if used > deps.settings.max_total_tokens:
        _write_state(state, deps, "budget_exhausted")
        raise PipelineError(
            "structure_token_budget_exhausted",
            f"structure run used {used} tokens, exceeding the configured "
            f"{deps.settings.max_total_tokens} token budget",
        )


def _write_state(state: StructureState, deps: StructureDeps, phase: str) -> None:
    report = state.audit
    payload = {
        "stage": "structure_agent",
        "status": (
            "success"
            if phase == "complete"
            else "failure"
            if phase == "budget_exhausted"
            else "running"
        ),
        "structure_version": deps.structure_version,
        "prompt_version": deps.prompt_version,
        "source_id": deps.source_id,
        "configured_model": deps.settings.primary_model,
        "phase": phase,
        "plan_revision": state.plan_revision,
        "repair_round": state.repair_round,
        "plan": state.plan.model_dump(mode="json") if state.plan else None,
        "completed_units": sorted(state.unit_results),
        "audit": (
            {
                "passed": report.passed,
                "issues": len(report.issues),
                "claimed_characters": report.claimed_characters,
                "source_characters": report.source_characters,
                "claimed_markers": report.claimed_markers,
                "source_markers": report.source_markers,
            }
            if report
            else None
        ),
    }
    write_json_result(
        deps.cache_root,
        payload,
        Path("structure-work") / deps.work_key / "agent-state.json",
    )


def _write_candidate(state: StructureState, deps: StructureDeps) -> Path:
    plan = state.plan
    report = state.audit
    if plan is None or report is None:
        raise PipelineError(
            "structure_state_invalid", "candidate requires a plan and audit"
        )
    units = []
    for unit in plan.units:
        result = state.unit_results.get(unit.unit_id)
        if result is None:
            raise PipelineError(
                "structure_state_invalid",
                f"candidate is missing unit {unit.unit_id}",
            )
        units.append(
            {
                "unit_id": unit.unit_id,
                "unit": unit.model_dump(mode="json"),
                "selected_pass": result.pass_name,
                "checkpoint_reused": result.checkpoint_reused,
                "validation_issue": (
                    result.validation_issue.model_dump(mode="json")
                    if result.validation_issue is not None
                    else None
                ),
                "draft": result.draft.model_dump(mode="json"),
            }
        )
    payload = {
        "stage": "structure_candidate",
        "status": "ready" if report.passed else "incomplete",
        "structure_version": deps.structure_version,
        "prompt_version": deps.prompt_version,
        "source_id": deps.source_id,
        "configured_model": deps.settings.primary_model,
        "plan_revision": state.plan_revision,
        "repair_round": state.repair_round,
        "plan": plan.model_dump(mode="json"),
        "audit": report.model_dump(mode="json"),
        "units": units,
        "passes": state.pass_records,
    }
    return write_json_result(
        deps.cache_root,
        payload,
        Path("structure-work") / deps.work_key / "candidate.json",
    )


def _load_candidate_state(deps: StructureDeps) -> StructureState:
    relative_path = Path("structure-work") / deps.work_key / "candidate.json"
    path = deps.cache_root / relative_path
    if not path.exists():
        return StructureState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload["stage"] != "structure_candidate"
            or payload["status"] not in {"incomplete", "ready"}
            or payload["structure_version"] != deps.structure_version
            or payload["prompt_version"] != deps.prompt_version
            or payload["source_id"] != deps.source_id
            or payload["configured_model"] != deps.settings.primary_model
            or payload["result_path"] != relative_path.as_posix()
        ):
            raise ValueError("candidate metadata does not match this workflow")
        plan = StructurePlan.model_validate(payload["plan"])
        deps.validate_plan(plan)
        raw_units = payload["units"]
        if not isinstance(raw_units, list):
            raise TypeError("candidate units must be a list")
        units_by_id = {item["unit_id"]: item for item in raw_units}
        if set(units_by_id) != {unit.unit_id for unit in plan.units}:
            raise ValueError("candidate units do not match its plan")
        unit_results: dict[str, UnitResult] = {}
        for unit in plan.units:
            item = units_by_id[unit.unit_id]
            stored_unit = StructureUnit.model_validate(item["unit"])
            if stored_unit != unit:
                raise ValueError(f"candidate unit {unit.unit_id} metadata changed")
            draft = StructureDraft.model_validate(item["draft"])
            try:
                deps.validate_unit(draft, unit)
            except PipelineError as exc:
                validation_issue = _issue_for_validation(unit, exc)
            else:
                validation_issue = None
            unit_results[unit.unit_id] = UnitResult(
                unit=unit,
                draft=draft,
                run=ModelRun(
                    output=draft,
                    model=deps.settings.primary_model,
                    output_mode="candidate_resume",
                    latency_seconds=0.0,
                    usage={"requests": 0, "input_tokens": 0, "output_tokens": 0},
                ),
                pass_name=item["selected_pass"],
                checkpoint_reused=True,
                validation_issue=validation_issue,
            )
        stored_pass_records = payload["passes"]
        if not isinstance(stored_pass_records, list) or any(
            not isinstance(record, dict) or not isinstance(record.get("usage"), dict)
            for record in stored_pass_records
        ):
            raise TypeError("candidate passes are invalid")
        pass_records = [
            {**record, "checkpoint_reused": True} for record in stored_pass_records
        ]
        state = StructureState(
            plan=plan,
            unit_results=unit_results,
            pass_records=pass_records,
            repair_round=int(payload["repair_round"]),
            plan_revision=int(payload.get("plan_revision", 0)),
        )
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PipelineError(
            "invalid_structure_candidate", f"cannot resume candidate: {exc}"
        ) from exc
    _emit(
        deps,
        "candidate_resumed",
        result_path=relative_path.as_posix(),
        units=len(state.unit_results),
        repair_round=state.repair_round,
    )
    return state


def _issue_for_validation(unit: StructureUnit, error: PipelineError) -> AuditIssue:
    return AuditIssue(
        code="unsupported_output",
        message=f"{error.code}: {error}",
        pdf_page=unit.start_pdf_page,
        unit_id=unit.unit_id,
    )


def _owner_for_page(plan: StructurePlan, page: int) -> str | None:
    candidates = [
        unit for unit in plan.units if unit.start_pdf_page <= page <= unit.end_pdf_page
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda unit: (
            unit.kind == "front_matter",
            unit.end_pdf_page - unit.start_pdf_page,
            unit.start_pdf_page != page,
        )
    )
    return candidates[0].unit_id


def _assign_issues(report: AuditReport, plan: StructurePlan) -> AuditReport:
    issues = [
        issue
        if issue.unit_id is not None or issue.pdf_page is None
        else issue.model_copy(update={"unit_id": _owner_for_page(plan, issue.pdf_page)})
        for issue in report.issues
    ]
    return report.model_copy(update={"issues": issues, "passed": not issues})


def _audit_results(
    plan: StructurePlan,
    unit_results: dict[str, UnitResult],
    deps: StructureDeps,
) -> AuditReport:
    ordered = [unit_results[unit.unit_id] for unit in plan.units]
    report = audit_drafts(
        [(result.unit.unit_id, result.draft) for result in ordered],
        pages=deps.pages,
        legal_start_pdf_page=plan.legal_start_pdf_page,
        legal_end_pdf_page=plan.legal_end_pdf_page,
        legal_end_terminator=plan.legal_end_terminator,
    )
    issues = [
        result.validation_issue
        for result in ordered
        if result.validation_issue is not None
    ] + report.issues
    return _assign_issues(
        report.model_copy(update={"issues": issues, "passed": not issues}), plan
    )


def _issue_summary(report: AuditReport) -> str:
    grouped: defaultdict[str, list[AuditIssue]] = defaultdict(list)
    for issue in report.issues:
        grouped[issue.unit_id or "unassigned"].append(issue)
    summary: list[dict[str, Any]] = []
    for unit_id, issues in sorted(grouped.items()):
        summary.append(
            {
                "unit_id": unit_id,
                "counts": dict(Counter(issue.code for issue in issues)),
                "samples": [issue.model_dump(mode="json") for issue in issues[:8]],
            }
        )
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


@dataclass
class PlanNode(BaseNode[StructureState, StructureDeps, WorkflowResult]):
    async def run(
        self, ctx: GraphRunContext[StructureState, StructureDeps]
    ) -> RunUnitsNode:
        previous_plan = ctx.state.plan
        previous_audit = ctx.state.audit
        pass_name = (
            "plan"
            if ctx.state.plan_revision == 0
            else f"plan-revision-{ctx.state.plan_revision}"
        )
        scope = ctx.deps.plan_scope(previous_plan, previous_audit)
        raw_text = ctx.deps.pages_text(1, ctx.deps.page_count)
        _emit(
            ctx.deps,
            "plan_started",
            pass_name=pass_name,
            plan_revision=ctx.state.plan_revision,
            input_characters=len(raw_text),
        )

        def validate(output: BaseModel) -> None:
            if not isinstance(output, StructurePlan):
                raise PipelineError(
                    "model_invalid_output", "planner returned the wrong schema"
                )
            ctx.deps.validate_plan(output)

        for attempt in _checkpoint_attempts(
            cache_root=ctx.deps.cache_root,
            work_key=ctx.deps.work_key,
            pass_index=ctx.state.plan_revision,
            pass_name=pass_name,
        ):
            attempt_name = pass_name if attempt == 0 else f"{pass_name}-retry-1"
            try:
                run, reused = await run_checkpointed(
                    cache_root=ctx.deps.cache_root,
                    work_key=ctx.deps.work_key,
                    pass_index=ctx.state.plan_revision,
                    pass_name=attempt_name,
                    source_id=ctx.deps.source_id,
                    raw_text=raw_text,
                    settings=ctx.deps.settings,
                    scope=scope,
                    output_type=StructurePlan,
                    runner=ctx.deps.runner,
                    validate=validate,
                    structure_version=ctx.deps.structure_version,
                    prompt_version=ctx.deps.prompt_version,
                )
                pass_name = attempt_name
                break
            except RejectedCheckpoint as exc:
                if attempt:
                    raise PipelineError(
                        "structure_plan_failed",
                        f"planner could not produce a valid plan: {exc.validation_error}",
                    ) from exc
                scope += (
                    "\n\nThe previous plan failed deterministic validation. Return a "
                    "complete corrected plan in a fresh response.\nVALIDATION ERROR\n"
                    f"{exc.validation_error.code}: {exc.validation_error}\n"
                    f"REJECTED PLAN\n{exc.output.model_dump_json()}"
                )
                _emit(
                    ctx.deps,
                    "plan_retrying",
                    pass_name=attempt_name,
                    error_code=exc.validation_error.code,
                )
            except PipelineError as exc:
                if not exc.retryable or attempt:
                    raise
                _emit(
                    ctx.deps,
                    "plan_retrying",
                    pass_name=attempt_name,
                    error_code=exc.code,
                )
                await asyncio.sleep(random.uniform(0.25, 1.0))
        else:
            raise AssertionError("planner request loop did not terminate")
        ctx.state.plan = StructurePlan.model_validate(run.output.model_dump())
        ctx.state.unit_results.clear()
        ctx.state.audit = None
        ctx.state.pass_records.append(_pass_record(pass_name, run, reused))
        _enforce_token_budget(ctx.state, ctx.deps)
        _write_state(ctx.state, ctx.deps, "planned")
        _emit(
            ctx.deps,
            "plan_completed",
            pass_name=pass_name,
            plan_revision=ctx.state.plan_revision,
            legal_start_pdf_page=ctx.state.plan.legal_start_pdf_page,
            legal_end_pdf_page=ctx.state.plan.legal_end_pdf_page,
            legal_end_terminator=ctx.state.plan.legal_end_terminator,
            units=len(ctx.state.plan.units),
            checkpoint_reused=reused,
            latency_seconds=run.latency_seconds,
            usage=run.usage,
        )
        return RunUnitsNode()


@dataclass
class RunUnitsNode(BaseNode[StructureState, StructureDeps, WorkflowResult]):
    async def run(
        self, ctx: GraphRunContext[StructureState, StructureDeps]
    ) -> AuditNode:
        plan = ctx.state.plan
        if plan is None:
            raise PipelineError("structure_state_invalid", "unit execution has no plan")
        semaphore = asyncio.Semaphore(ctx.deps.settings.concurrency)
        _emit(
            ctx.deps,
            "units_started",
            plan_revision=ctx.state.plan_revision,
            units=len(plan.units),
            concurrency=ctx.deps.settings.concurrency,
        )

        async def run_unit(index: int, unit: StructureUnit) -> UnitResult:
            existing = ctx.state.unit_results.get(unit.unit_id)
            if existing is not None:
                return existing
            pass_index = ctx.state.plan_revision * 100 + index
            raw_text = ctx.deps.pages_text(unit.start_pdf_page, unit.end_pdf_page)
            scope = ctx.deps.unit_scope(unit, [], None)

            def validate(output: BaseModel) -> None:
                if not isinstance(output, StructureDraft):
                    raise PipelineError(
                        "model_invalid_output", "unit returned the wrong schema"
                    )
                ctx.deps.validate_unit(output, unit)

            async with semaphore:
                _emit(
                    ctx.deps,
                    "unit_started",
                    unit_id=unit.unit_id,
                    kind=unit.kind,
                    start_pdf_page=unit.start_pdf_page,
                    end_pdf_page=unit.end_pdf_page,
                )
                for request_attempt in _checkpoint_attempts(
                    cache_root=ctx.deps.cache_root,
                    work_key=ctx.deps.work_key,
                    pass_index=pass_index,
                    pass_name=unit.unit_id,
                ):
                    suffix = "" if request_attempt == 0 else "-retry-1"
                    pass_name = f"{unit.unit_id}{suffix}"
                    try:
                        run, reused = await run_checkpointed(
                            cache_root=ctx.deps.cache_root,
                            work_key=ctx.deps.work_key,
                            pass_index=pass_index,
                            pass_name=pass_name,
                            source_id=ctx.deps.source_id,
                            raw_text=raw_text,
                            settings=ctx.deps.settings,
                            scope=scope,
                            output_type=StructureDraft,
                            runner=ctx.deps.runner,
                            validate=validate,
                            structure_version=ctx.deps.structure_version,
                            prompt_version=ctx.deps.prompt_version,
                        )
                        try:
                            revalidated = StructureDraft.model_validate(
                                run.output.model_dump()
                            )
                        except ValidationError as exc:
                            # A draft that will not survive a round trip must not
                            # reach the graph, and must not abort the whole run.
                            raise PipelineError(
                                "model_invalid_output",
                                f"{unit.unit_id} draft does not revalidate: "
                                f"{str(exc)[:600]}",
                                retryable=True,
                            ) from exc
                        result = UnitResult(
                            unit=unit,
                            draft=revalidated,
                            run=run,
                            pass_name=pass_name,
                            checkpoint_reused=reused,
                        )
                        _emit(
                            ctx.deps,
                            "unit_completed",
                            unit_id=unit.unit_id,
                            pass_name=pass_name,
                            checkpoint_reused=reused,
                            latency_seconds=run.latency_seconds,
                            output_mode=run.output_mode,
                            usage=run.usage,
                        )
                        return result
                    except RejectedCheckpoint as exc:
                        rejected = StructureDraft.model_validate(
                            exc.output.model_dump()
                        )
                        result = UnitResult(
                            unit=unit,
                            draft=rejected,
                            run=ModelRun(
                                output=rejected,
                                model=ctx.deps.settings.primary_model,
                                output_mode="tool",
                                latency_seconds=0,
                                usage={
                                    "requests": 1,
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                },
                            ),
                            pass_name=pass_name,
                            checkpoint_reused=exc.checkpoint_reused,
                            validation_issue=_issue_for_validation(
                                unit, exc.validation_error
                            ),
                        )
                        _emit(
                            ctx.deps,
                            "unit_rejected",
                            unit_id=unit.unit_id,
                            pass_name=pass_name,
                            checkpoint_reused=exc.checkpoint_reused,
                            error_code=exc.validation_error.code,
                            rejected_checkpoint=exc.relative_path.as_posix(),
                        )
                        return result
                    except PipelineError as exc:
                        if not exc.retryable:
                            raise
                        if request_attempt:
                            # One unhelpful unit is not a reason to discard the
                            # twelve that worked. The audit reports its pages as
                            # unclaimed, which is what review needs to see.
                            _emit(
                                ctx.deps,
                                "unit_failed",
                                unit_id=unit.unit_id,
                                pass_name=pass_name,
                                error_code=exc.code,
                                detail=str(exc)[:400],
                            )
                            empty = StructureDraft(nodes=[])
                            return UnitResult(
                                unit=unit,
                                draft=empty,
                                run=ModelRun(
                                    output=empty,
                                    model=ctx.deps.settings.primary_model,
                                    output_mode="failed",
                                    latency_seconds=0,
                                    usage={
                                        "requests": 0,
                                        "input_tokens": 0,
                                        "output_tokens": 0,
                                    },
                                ),
                                pass_name=pass_name,
                                checkpoint_reused=False,
                                validation_issue=_issue_for_validation(unit, exc),
                            )
                        _emit(
                            ctx.deps,
                            "unit_retrying",
                            unit_id=unit.unit_id,
                            pass_name=pass_name,
                            error_code=exc.code,
                        )
                        await asyncio.sleep(random.uniform(0.25, 1.0))
                raise AssertionError("unit request loop did not terminate")

        results = await asyncio.gather(
            *(run_unit(index, unit) for index, unit in enumerate(plan.units, start=1))
        )
        for result in results:
            ctx.state.unit_results[result.unit.unit_id] = result
            ctx.state.pass_records.append(
                _pass_record(
                    result.pass_name,
                    result.run,
                    result.checkpoint_reused,
                    unit=result.unit,
                )
            )
        _enforce_token_budget(ctx.state, ctx.deps)
        _write_state(ctx.state, ctx.deps, "units_structured")
        _emit(
            ctx.deps,
            "units_completed",
            plan_revision=ctx.state.plan_revision,
            units=len(results),
        )
        return AuditNode()


@dataclass
class AuditNode(BaseNode[StructureState, StructureDeps, WorkflowResult]):
    async def run(
        self, ctx: GraphRunContext[StructureState, StructureDeps]
    ) -> CriticNode | FinalizeNode:
        plan = ctx.state.plan
        if plan is None:
            raise PipelineError("structure_state_invalid", "audit has no plan")
        ctx.state.audit = _audit_results(plan, ctx.state.unit_results, ctx.deps)
        audit_payload = {
            "stage": "structure_audit",
            "status": "success" if ctx.state.audit.passed else "failure",
            "source_id": ctx.deps.source_id,
            "structure_version": ctx.deps.structure_version,
            "prompt_version": ctx.deps.prompt_version,
            "report": ctx.state.audit.model_dump(mode="json"),
        }
        write_json_result(
            ctx.deps.cache_root,
            audit_payload,
            Path("structure-work") / ctx.deps.work_key / "audit.json",
        )
        _write_state(ctx.state, ctx.deps, "audited")
        candidate_path = _write_candidate(ctx.state, ctx.deps)
        candidate_relative_path = candidate_path.relative_to(ctx.deps.cache_root)
        _emit(
            ctx.deps,
            "audit_completed",
            passed=ctx.state.audit.passed,
            issues=len(ctx.state.audit.issues),
            claimed_characters=ctx.state.audit.claimed_characters,
            source_characters=ctx.state.audit.source_characters,
            claimed_markers=ctx.state.audit.claimed_markers,
            source_markers=ctx.state.audit.source_markers,
            repair_round=ctx.state.repair_round,
        )
        _emit(
            ctx.deps,
            "candidate_written",
            status="ready" if ctx.state.audit.passed else "incomplete",
            result_path=candidate_relative_path.as_posix(),
            issues=len(ctx.state.audit.issues),
            repair_round=ctx.state.repair_round,
        )
        if ctx.state.audit.passed:
            return FinalizeNode()
        if ctx.state.repair_round >= ctx.deps.settings.max_repair_rounds:
            if ctx.state.audit.reviewable:
                _emit(
                    ctx.deps,
                    "audit_findings_deferred_to_review",
                    issues=len(ctx.state.audit.issues),
                    variances=len(ctx.state.audit.variances),
                    source_coverage=round(ctx.state.audit.source_coverage, 5),
                )
                return FinalizeNode()
            raise PipelineError(
                "structure_audit_failed",
                f"structuring accounted for only "
                f"{ctx.state.audit.source_coverage:.1%} of the source after "
                f"{ctx.state.repair_round} repair rounds; "
                f"candidate: {candidate_relative_path.as_posix()}; "
                f"audit: structure-work/{ctx.deps.work_key}/audit.json",
            )
        return CriticNode()


@dataclass
class CriticNode(BaseNode[StructureState, StructureDeps, WorkflowResult]):
    async def run(
        self, ctx: GraphRunContext[StructureState, StructureDeps]
    ) -> RepairNode | PlanNode | FinalizeNode:
        plan = ctx.state.plan
        report = ctx.state.audit
        if plan is None or report is None:
            raise PipelineError("structure_state_invalid", "critic has no audit")
        affected = sorted(
            {issue.unit_id for issue in report.issues if issue.unit_id is not None}
        )
        if not affected:
            raise PipelineError(
                "structure_audit_failed",
                "audit issues could not be assigned to a semantic unit",
            )
        critic_scope = (
            "You are the critic for a legal-document structuring agent. Decide how "
            "to recover from deterministic audit failures. Use replace_unit for "
            "local omissions or hierarchy errors, replan_document only when unit "
            "boundaries or document scope are wrong, and abort_unresolved only when "
            "the source cannot be faithfully reconstructed. Return exactly one "
            "decision for every affected unit unless replanning the whole document.\n"
            f"Affected units: {json.dumps(affected)}\n"
            f"Current plan: {plan.model_dump_json()}\n"
            f"Audit: {_issue_summary(report)}"
        )

        def validate(output: BaseModel) -> None:
            repair = RepairPlan.model_validate(output.model_dump())
            decisions = repair.decisions
            if any(decision.action == "replan_document" for decision in decisions):
                if len(decisions) != 1:
                    raise PipelineError(
                        "invalid_repair_plan",
                        "replan_document must be the only decision",
                    )
                return
            decided = [decision.unit_id for decision in decisions]
            if sorted(decided) != affected or len(decided) != len(set(decided)):
                raise PipelineError(
                    "invalid_repair_plan",
                    "repair plan must decide each affected unit exactly once",
                )

        critic_round = ctx.state.repair_round + 1
        pass_name = f"critic-{critic_round}"
        _emit(
            ctx.deps,
            "critic_started",
            pass_name=pass_name,
            repair_round=critic_round,
            affected_units=affected,
            issues=len(report.issues),
        )
        for attempt in _checkpoint_attempts(
            cache_root=ctx.deps.cache_root,
            work_key=ctx.deps.work_key,
            pass_index=1000 + critic_round,
            pass_name=pass_name,
        ):
            attempt_name = pass_name if attempt == 0 else f"{pass_name}-retry-1"
            try:
                run, reused = await run_checkpointed(
                    cache_root=ctx.deps.cache_root,
                    work_key=ctx.deps.work_key,
                    pass_index=1000 + critic_round,
                    pass_name=attempt_name,
                    source_id=ctx.deps.source_id,
                    raw_text=_issue_summary(report),
                    settings=ctx.deps.settings,
                    scope=critic_scope,
                    output_type=RepairPlan,
                    runner=ctx.deps.runner,
                    validate=validate,
                    structure_version=ctx.deps.structure_version,
                    prompt_version=ctx.deps.prompt_version,
                )
                pass_name = attempt_name
                break
            except RejectedCheckpoint as exc:
                if attempt:
                    raise PipelineError(
                        "structure_critic_failed",
                        f"critic could not produce a valid repair plan: "
                        f"{exc.validation_error}",
                    ) from exc
                critic_scope += (
                    "\n\nThe previous repair plan failed deterministic validation. "
                    "Return a complete corrected RepairPlan.\nVALIDATION ERROR\n"
                    f"{exc.validation_error.code}: {exc.validation_error}"
                )
                _emit(
                    ctx.deps,
                    "critic_retrying",
                    pass_name=attempt_name,
                    error_code=exc.validation_error.code,
                )
            except PipelineError as exc:
                if not exc.retryable:
                    raise
                if attempt:
                    # Repair is an improvement on a draft that already exists.
                    # An unreachable critic is not a reason to discard it; the
                    # audit is the authority on whether it can be reviewed.
                    _emit(
                        ctx.deps,
                        "critic_unavailable",
                        pass_name=attempt_name,
                        error_code=exc.code,
                        detail=str(exc)[:400],
                    )
                    return FinalizeNode()
                _emit(
                    ctx.deps,
                    "critic_retrying",
                    pass_name=attempt_name,
                    error_code=exc.code,
                )
                await asyncio.sleep(random.uniform(0.25, 1.0))
        else:
            raise AssertionError("critic request loop did not terminate")
        repair = RepairPlan.model_validate(run.output.model_dump())
        ctx.state.pass_records.append(_pass_record(pass_name, run, reused))
        _enforce_token_budget(ctx.state, ctx.deps)
        _emit(
            ctx.deps,
            "critic_completed",
            pass_name=pass_name,
            checkpoint_reused=reused,
            decisions=[
                {"unit_id": decision.unit_id, "action": decision.action}
                for decision in repair.decisions
            ],
            latency_seconds=run.latency_seconds,
            usage=run.usage,
        )
        if any(decision.action == "abort_unresolved" for decision in repair.decisions):
            reasons = "; ".join(
                decision.reason
                for decision in repair.decisions
                if decision.action == "abort_unresolved"
            )
            raise PipelineError("structure_audit_failed", reasons)
        if repair.decisions[0].action == "replan_document":
            ctx.state.plan_revision += 1
            ctx.state.repair_round += 1
            _write_state(ctx.state, ctx.deps, "replanning")
            _emit(
                ctx.deps,
                "replan_started",
                plan_revision=ctx.state.plan_revision,
                repair_round=ctx.state.repair_round,
            )
            return PlanNode()
        return RepairNode(repair=repair)


@dataclass
class RepairNode(BaseNode[StructureState, StructureDeps, WorkflowResult]):
    repair: RepairPlan

    async def run(
        self, ctx: GraphRunContext[StructureState, StructureDeps]
    ) -> AuditNode:
        plan = ctx.state.plan
        report = ctx.state.audit
        if plan is None or report is None:
            raise PipelineError("structure_state_invalid", "repair has no audit")
        ctx.state.repair_round += 1
        round_number = ctx.state.repair_round
        semaphore = asyncio.Semaphore(ctx.deps.settings.concurrency)
        decisions = {decision.unit_id: decision for decision in self.repair.decisions}
        _emit(
            ctx.deps,
            "repairs_started",
            repair_round=round_number,
            units=sorted(decisions),
            concurrency=ctx.deps.settings.concurrency,
        )

        async def repair_unit(index: int, unit: StructureUnit) -> UnitResult | None:
            current = ctx.state.unit_results[unit.unit_id]
            issues = [issue for issue in report.issues if issue.unit_id == unit.unit_id]
            scope = ctx.deps.repair_scope(unit, issues, current.draft)
            raw_text = ctx.deps.pages_text(unit.start_pdf_page, unit.end_pdf_page)

            def patched_draft(output: BaseModel) -> StructureDraft:
                if not isinstance(output, RepairPatch):
                    raise PipelineError(
                        "model_invalid_output", "repair returned the wrong patch schema"
                    )
                if output.unit_id != unit.unit_id:
                    raise PipelineError(
                        "invalid_repair_patch",
                        f"repair patch targets {output.unit_id}, not {unit.unit_id}",
                    )
                candidate = apply_repair_patch(current.draft, output)
                ctx.deps.validate_unit(candidate, unit)
                return candidate

            def validate(output: BaseModel) -> None:
                patched_draft(output)

            async with semaphore:
                _emit(
                    ctx.deps,
                    "repair_patch_started",
                    repair_round=round_number,
                    unit_id=unit.unit_id,
                    issues=len(issues),
                )
                repair_pass_name = f"{unit.unit_id}-patch-repair-{round_number}"
                pass_index = 3000 + round_number * 100 + index
                for request_attempt in _checkpoint_attempts(
                    cache_root=ctx.deps.cache_root,
                    work_key=ctx.deps.work_key,
                    pass_index=pass_index,
                    pass_name=repair_pass_name,
                ):
                    suffix = "" if request_attempt == 0 else "-retry-1"
                    pass_name = f"{repair_pass_name}{suffix}"
                    try:
                        run, reused = await run_checkpointed(
                            cache_root=ctx.deps.cache_root,
                            work_key=ctx.deps.work_key,
                            pass_index=pass_index,
                            pass_name=pass_name,
                            source_id=ctx.deps.source_id,
                            raw_text=raw_text,
                            settings=ctx.deps.settings,
                            scope=scope,
                            output_type=RepairPatch,
                            runner=ctx.deps.runner,
                            validate=validate,
                            structure_version=ctx.deps.structure_version,
                            prompt_version=ctx.deps.prompt_version,
                        )
                        result = UnitResult(
                            unit=unit,
                            draft=patched_draft(run.output),
                            run=run,
                            pass_name=pass_name,
                            checkpoint_reused=reused,
                        )
                        _emit(
                            ctx.deps,
                            "repair_patch_validated",
                            repair_round=round_number,
                            unit_id=unit.unit_id,
                            pass_name=pass_name,
                            checkpoint_reused=reused,
                            latency_seconds=run.latency_seconds,
                            usage=run.usage,
                        )
                        return result
                    except RejectedCheckpoint as exc:
                        _emit(
                            ctx.deps,
                            "repair_patch_discarded",
                            repair_round=round_number,
                            unit_id=unit.unit_id,
                            pass_name=pass_name,
                            checkpoint_reused=exc.checkpoint_reused,
                            reason="validation_failed",
                            error_code=exc.validation_error.code,
                            rejected_checkpoint=exc.relative_path.as_posix(),
                        )
                        return None
                    except PipelineError as exc:
                        if not exc.retryable or request_attempt:
                            raise
                        _emit(
                            ctx.deps,
                            "repair_patch_retrying",
                            repair_round=round_number,
                            unit_id=unit.unit_id,
                            pass_name=pass_name,
                            error_code=exc.code,
                        )
                        await asyncio.sleep(random.uniform(0.25, 1.0))
                raise AssertionError("repair request loop did not terminate")

        unit_indexes = {
            unit.unit_id: index for index, unit in enumerate(plan.units, start=1)
        }
        targets = [unit for unit in plan.units if unit.unit_id in decisions]
        replacements = await asyncio.gather(
            *(repair_unit(unit_indexes[unit.unit_id], unit) for unit in targets)
        )
        working_report = report
        accepted = 0
        discarded = sum(result is None for result in replacements)
        for result in replacements:
            if result is None:
                continue
            ctx.state.pass_records.append(
                _pass_record(
                    result.pass_name,
                    result.run,
                    result.checkpoint_reused,
                    unit=result.unit,
                )
            )
            trial_results = dict(ctx.state.unit_results)
            trial_results[result.unit.unit_id] = result
            trial_report = _audit_results(plan, trial_results, ctx.deps)
            if repair_improves(working_report, trial_report, result.unit.unit_id):
                before_score = repair_score(working_report)
                ctx.state.unit_results[result.unit.unit_id] = result
                working_report = trial_report
                accepted += 1
                _emit(
                    ctx.deps,
                    "repair_patch_accepted",
                    repair_round=round_number,
                    unit_id=result.unit.unit_id,
                    pass_name=result.pass_name,
                    before_score=before_score,
                    after_score=repair_score(trial_report),
                )
            else:
                discarded += 1
                _emit(
                    ctx.deps,
                    "repair_patch_discarded",
                    repair_round=round_number,
                    unit_id=result.unit.unit_id,
                    pass_name=result.pass_name,
                    reason="audit_not_improved",
                    before_score=repair_score(working_report),
                    after_score=repair_score(trial_report),
                )
        _enforce_token_budget(ctx.state, ctx.deps)
        ctx.state.audit = working_report
        _write_state(ctx.state, ctx.deps, "repaired")
        _emit(
            ctx.deps,
            "repairs_completed",
            repair_round=round_number,
            units=len(targets),
            accepted=accepted,
            discarded=discarded,
        )
        return AuditNode()


@dataclass
class FinalizeNode(BaseNode[StructureState, StructureDeps, WorkflowResult]):
    async def run(
        self, ctx: GraphRunContext[StructureState, StructureDeps]
    ) -> End[WorkflowResult]:
        plan = ctx.state.plan
        report = ctx.state.audit
        if plan is None or report is None or not (report.passed or report.reviewable):
            raise PipelineError(
                "structure_audit_failed",
                "finish gate requires an audit the reviewer can act on",
            )
        drafts = [ctx.state.unit_results[unit.unit_id].draft for unit in plan.units]
        _write_state(ctx.state, ctx.deps, "complete")
        _emit(
            ctx.deps,
            "graph_completed",
            units=len(plan.units),
            repair_rounds=ctx.state.repair_round,
        )
        return End(
            WorkflowResult(
                plan=plan,
                drafts=drafts,
                audit=report,
                pass_records=ctx.state.pass_records,
                repair_rounds=ctx.state.repair_round,
            )
        )


@dataclass
class ResumeNode(BaseNode[StructureState, StructureDeps, WorkflowResult]):
    async def run(
        self, ctx: GraphRunContext[StructureState, StructureDeps]
    ) -> PlanNode | AuditNode:
        if ctx.state.plan is not None and ctx.state.unit_results:
            return AuditNode()
        return PlanNode()


def build_structure_graph():
    builder = GraphBuilder(
        state_type=StructureState,
        deps_type=StructureDeps,
        output_type=WorkflowResult,
    )

    @builder.step
    async def start(
        ctx: StepContext[StructureState, StructureDeps, None],
    ) -> ResumeNode:
        return ResumeNode()

    builder.add(
        builder.node(PlanNode),
        builder.node(RunUnitsNode),
        builder.node(AuditNode),
        builder.node(CriticNode),
        builder.node(RepairNode),
        builder.node(FinalizeNode),
        builder.node(ResumeNode),
        builder.edge_from(builder.start_node).to(start),
    )
    return builder.build()


STRUCTURE_GRAPH = build_structure_graph()


async def run_structure_graph(deps: StructureDeps) -> WorkflowResult:
    return await STRUCTURE_GRAPH.run(state=_load_candidate_state(deps), deps=deps)
