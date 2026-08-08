"""Turn extracted PDF text into complete, source-backed Provision drafts."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import re
import time
import unicodedata
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent, ModelRetry, RunUsage, ToolOutput, UsageLimits
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from openacts_pipeline.common import (
    PipelineError,
    iso_timestamp,
    utc_now,
    write_json_result,
)
from openacts_pipeline.config import DEFAULT_PRIMARY_MODEL, StructureSettings
from openacts_pipeline.structure_audit import (
    MARKER_LABEL,
    audit_materialized_provisions,
)
from openacts_pipeline.structure_prompts import (
    CRITIC_INSTRUCTIONS,
    PLAN_INSTRUCTIONS,
    SYSTEM_INSTRUCTIONS,
)
from openacts_pipeline.structure_runtime import (
    ModelRun,
    ModelRunner,
    ProgressReporter,
    RejectedModelOutput,
    StructureDeps,
    run_structure_graph,
)
from openacts_pipeline.structure_schema import (
    DraftListBlock,
    DraftTableBlock,
    DraftTextBlock,
    RepairPlan,
    StructureDraft,
    StructurePlan,
    StructureUnit,
    iter_block_pages,
    iter_block_texts,
    materialize_provisions,
    validate_table,
)

STRUCTURE_VERSION = 6
SUPPORTED_EXTRACTION_VERSIONS = {1, 2}
PROMPT_VERSION = 24
MAX_OUTPUT_TOKENS = 384_000
TRANSIENT_MODEL_STATUSES = {408, 429, 500, 502, 503, 504}

# Compatibility for callers and preserved tests that inspect rejected drafts.
_RejectedModelOutput = RejectedModelOutput


DOCUMENT_NODE_TYPES = {
    "document_title",
    "long_title",
    "preamble",
    "enacting_formula",
    "part",
    "chapter",
    "division",
    "cross_heading",
    "section",
    "subsection",
    "paragraph",
    "subparagraph",
    "definition",
    "table",
    "form",
    "schedule",
    "schedule_part",
    "schedule_paragraph",
    "schedule_subparagraph",
}
FRONT_NODE_TYPES = {
    "document_title",
    "long_title",
    "preamble",
    "enacting_formula",
}
BODY_NODE_TYPES = (
    DOCUMENT_NODE_TYPES
    - FRONT_NODE_TYPES
    - {
        "schedule",
        "schedule_part",
        "schedule_paragraph",
        "schedule_subparagraph",
    }
)
UNIT_NODE_TYPES = {
    "front_matter": FRONT_NODE_TYPES,
    "body": BODY_NODE_TYPES,
    "chapter": BODY_NODE_TYPES,
    "part": BODY_NODE_TYPES,
    "schedule": {
        "schedule",
        "schedule_part",
        "schedule_paragraph",
        "schedule_subparagraph",
        "paragraph",
        "subparagraph",
        "definition",
        "cross_heading",
        "table",
        "form",
    },
}
CONTENT_OPTIONAL_LEAVES = {
    "document_title",
    "part",
    "chapter",
    "division",
    "cross_heading",
    "schedule",
    "schedule_part",
}
ADDRESSABLE_MARKER = re.compile(
    r"(?:^|\n|[—–:]|-\s+)\s*"
    r"(?:\(\d+[a-z]?\)|\([a-z]\)|\([ivxlcdm]+\)|\d+[a-z]?\.)\s+",
    re.IGNORECASE,
)
PLAN_SCOPE_MARKER = re.compile(
    r"(?m)^[ \t]*(?:\d+[a-z]?\.[ \t]*(?:[—–-][ \t]*)?)?"
    r"\((?:\d+[a-z]?|[a-z]+|[ivxlcdm]+)[ \t]*\)",
    re.IGNORECASE,
)
EXCLUDED_PLAN_HEADING = re.compile(
    r"(?im)^[ \t]*(foreword|preface|arrangement of "
    r"(?:sections|articles|regulations|rules)|table of contents|"
    r"explanatory (?:memorandum|note))[ \t]*$"
)
STRUCTURAL_LIST_STYLES = {
    "decimal",
    "lower_alpha",
    "upper_alpha",
    "lower_roman",
    "upper_roman",
}
PARENT_NODE_TYPES = {
    "section": {"part", "chapter", "division", "cross_heading"},
    "subsection": {"section"},
    "paragraph": {"section", "subsection", "definition", "schedule_subparagraph"},
    "subparagraph": {"paragraph"},
    "schedule_part": {"schedule"},
    "schedule_paragraph": {"schedule", "schedule_part", "cross_heading"},
    "schedule_subparagraph": {"schedule_paragraph"},
}
PARENT_REQUIRED_NODE_TYPES = set(PARENT_NODE_TYPES) - {"section"}

DEEPSEEK_PROFILE = cast(
    OpenAIModelProfile,
    {
        "supports_tools": True,
        "supports_thinking": True,
        "openai_chat_thinking_field": "reasoning_content",
        "openai_chat_supports_max_completion_tokens": False,
        "openai_supports_strict_tool_definition": False,
    },
)


def _load_extraction(
    extraction_path: Path, cache_root: Path
) -> tuple[dict[str, Any], Path]:
    try:
        relative_path = extraction_path.resolve().relative_to(cache_root.resolve())
    except ValueError as exc:
        raise PipelineError(
            "invalid_extraction", "extraction must be inside the cache root"
        ) from exc
    if not relative_path.parts or relative_path.parts[0] != "extractions":
        raise PipelineError(
            "invalid_extraction", "input must be an extraction artifact"
        )

    try:
        artifact = json.loads(extraction_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(
            "invalid_extraction", f"cannot read extraction: {exc}"
        ) from exc
    if (
        not isinstance(artifact, dict)
        or artifact.get("stage") != "extract"
        or artifact.get("status") != "success"
    ):
        raise PipelineError(
            "invalid_extraction", "input must be a successful extraction"
        )
    if artifact.get("result_path") != relative_path.as_posix():
        raise PipelineError(
            "invalid_extraction", "extraction result_path does not match input"
        )
    if artifact.get("extraction_version") not in SUPPORTED_EXTRACTION_VERSIONS:
        raise PipelineError(
            "unsupported_extraction_version", "extraction version is unsupported"
        )

    source_id = artifact.get("source_id")
    if not isinstance(source_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", source_id
    ):
        raise PipelineError("invalid_extraction", "source_id must use SHA-256")
    page_count = artifact.get("page_count")
    pages = artifact.get("pages")
    if (
        not isinstance(page_count, int)
        or isinstance(page_count, bool)
        or page_count < 1
        or not isinstance(pages, list)
        or len(pages) != page_count
    ):
        raise PipelineError(
            "invalid_extraction", "extraction page count is inconsistent"
        )
    for pdf_page, page in enumerate(pages, start=1):
        if (
            not isinstance(page, dict)
            or page.get("pdf_page") != pdf_page
            or not isinstance(page.get("text"), str)
            or page.get("text_characters") != len(page["text"])
        ):
            raise PipelineError(
                "invalid_extraction", "extraction page evidence is inconsistent"
            )
    return artifact, relative_path


def build_raw_text(pages: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"--- PDF PAGE {page['pdf_page']} ---\n{page['text']}" for page in pages
    )


def _client(settings: StructureSettings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        max_retries=0,
        timeout=settings.request_timeout_seconds,
    )


def _model(
    name: str,
    settings: StructureSettings,
    *,
    client: AsyncOpenAI | None = None,
) -> OpenAIChatModel:
    return OpenAIChatModel(
        name,
        provider=OpenAIProvider(openai_client=client or _client(settings)),
        profile=DEEPSEEK_PROFILE,
    )


def _usage(result: Any) -> dict[str, int]:
    usage = result.usage
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def _recover_trailing_json_delimiters(
    error: UnexpectedModelBehavior, output_type: type[BaseModel]
) -> BaseModel | None:
    cause = error.__cause__
    if not isinstance(cause, ValidationError):
        return None
    for issue in cause.errors(include_url=False):
        raw = issue.get("input")
        if issue.get("type") != "json_invalid" or not isinstance(raw, str):
            continue
        try:
            start = len(raw) - len(raw.lstrip())
            decoded, end = json.JSONDecoder().raw_decode(raw, idx=start)
        except (json.JSONDecodeError, TypeError):
            continue
        trailing = raw[end:].strip()
        if not trailing or any(character not in "]}" for character in trailing):
            continue
        try:
            return output_type.model_validate(decoded)
        except ValidationError:
            continue
    return None


async def _run_agent(
    raw_text: str,
    settings: StructureSettings,
    scope: str,
    output_type: type[BaseModel],
    validate: Any = None,
    model: OpenAIChatModel | None = None,
) -> ModelRun:
    rejected_output: BaseModel | None = None
    validation_error: PipelineError | None = None
    instructions = (
        PLAN_INSTRUCTIONS
        if output_type is StructurePlan
        else CRITIC_INSTRUCTIONS
        if output_type is RepairPlan
        else SYSTEM_INSTRUCTIONS
    )
    agent = Agent(
        model or _model(settings.primary_model, settings),
        output_type=ToolOutput(output_type, strict=False, max_retries=0),
        instructions=f"{instructions}\n\nREQUEST SCOPE\n{scope}",
        # Repair is a fresh graph pass. In-conversation retries would replay a
        # rejected legal tree into the next request and waste the context window.
        retries=0,
        model_settings={
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )
    if validate is not None:

        @agent.output_validator
        def validate_source(output: BaseModel) -> BaseModel:
            nonlocal rejected_output, validation_error
            try:
                validate(output)
            except PipelineError as exc:
                rejected_output = output
                validation_error = exc
                raise ModelRetry(f"{exc.code}: {exc}") from exc
            return output

    started = time.perf_counter()
    run_usage = RunUsage()
    try:
        result = await agent.run(
            raw_text,
            usage=run_usage,
            usage_limits=UsageLimits(request_limit=1),
        )
        output = result.output
        usage = _usage(result)
    except ModelHTTPError as exc:
        retryable = exc.status_code in TRANSIENT_MODEL_STATUSES
        detail = str(exc.body).replace("\n", " ")[:500]
        raise PipelineError(
            "model_transient" if retryable else "model_http_error",
            f"{settings.primary_model} returned HTTP {exc.status_code}: {detail}",
            retryable=retryable,
        ) from exc
    except UsageLimitExceeded as exc:
        raise PipelineError(
            "model_usage_limit",
            f"{settings.primary_model} exceeded the single-request agent limit "
            f"after {run_usage.requests} request(s), {run_usage.input_tokens} input "
            f"tokens, and {run_usage.output_tokens} output tokens: {exc}",
        ) from exc
    except UnexpectedModelBehavior as exc:
        if rejected_output is not None and validation_error is not None:
            raise RejectedModelOutput(
                settings.primary_model,
                rejected_output,
                validation_error,
            ) from exc
        if isinstance(exc, IncompleteToolCall):
            raise PipelineError(
                "model_output_truncated",
                f"{settings.primary_model} exhausted its output token budget: {exc}",
                retryable=True,
            ) from exc
        recovered = _recover_trailing_json_delimiters(exc, output_type)
        if recovered is not None:
            if validate is not None:
                try:
                    validate(recovered)
                except PipelineError as validation_exc:
                    raise RejectedModelOutput(
                        settings.primary_model,
                        recovered,
                        validation_exc,
                    ) from exc
            return ModelRun(
                output=recovered,
                model=settings.primary_model,
                output_mode="tool_recovered_trailing_delimiters",
                latency_seconds=round(time.perf_counter() - started, 3),
                usage={
                    "requests": run_usage.requests,
                    "input_tokens": run_usage.input_tokens,
                    "output_tokens": run_usage.output_tokens,
                },
            )
        detail = str(exc)
        if exc.__cause__ is not None:
            detail += f"; validation cause: {exc.__cause__}"
        detail = re.sub(r"\s+", " ", detail)[:2000]
        raise PipelineError(
            "model_invalid_output",
            f"{settings.primary_model} did not return valid structured output: {detail}",
            retryable=True,
        ) from exc
    except ModelAPIError as exc:
        raise PipelineError(
            "model_transient",
            f"{settings.primary_model} request failed: {exc}",
            retryable=True,
        ) from exc
    if not isinstance(output, output_type):
        raise PipelineError("model_invalid_output", "model returned the wrong schema")
    return ModelRun(
        output=output,
        model=settings.primary_model,
        output_mode="tool",
        latency_seconds=round(time.perf_counter() - started, 3),
        usage=usage,
    )


async def _run_primary(
    raw_text: str,
    settings: StructureSettings,
    scope: str,
    output_type: type[BaseModel],
    validate: Any = None,
) -> ModelRun:
    return await _run_agent(raw_text, settings, scope, output_type, validate)


def _normalized(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value).casefold()
        if character.isalnum()
    )


def _is_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    position = 0
    for character in haystack:
        if character == needle[position]:
            position += 1
            if position == len(needle):
                return True
    return False


def _page_source(pages: list[dict[str, Any]], pdf_pages: Iterable[int]) -> str:
    wanted = set(pdf_pages)
    return "\n".join(page["text"] for page in pages if page["pdf_page"] in wanted)


def _hides_addressable_marker(block: Any) -> bool:
    if isinstance(block, DraftTextBlock):
        return ADDRESSABLE_MARKER.search(block.text) is not None
    if isinstance(block, DraftListBlock):
        return (
            block.marker_style in STRUCTURAL_LIST_STYLES
            or any(
                item.label is not None
                and MARKER_LABEL.fullmatch(item.label.strip()) is not None
                for item in block.items
            )
            or any(
                _hides_addressable_marker(child)
                for item in block.items
                for child in item.content_blocks
            )
        )
    return False


def _walk_nodes(nodes: Iterable[Any]) -> Iterable[Any]:
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)


def _node_reference(node: Any) -> str:
    visible = node.display_label or node.heading
    return f"{node.node_type} {visible}" if visible else f"unnumbered {node.node_type}"


def _validate_draft(
    draft: StructureDraft,
    *,
    allowed_types: set[str],
    page_count: int,
    pages: list[dict[str, Any]],
    target: StructureUnit | None = None,
) -> None:
    if target is not None and target.kind == "schedule":
        # Printed Parts and numbered provisions retain their meaning inside a
        # Schedule; normalize provider vocabulary to the canonical context.
        for node in _walk_nodes(draft.nodes):
            if node.node_type == "part":
                node.node_type = "schedule_part"
            elif node.node_type == "section":
                node.node_type = "schedule_paragraph"
            elif node.node_type == "subsection":
                node.node_type = "schedule_subparagraph"
    all_nodes = list(_walk_nodes(draft.nodes))
    unexpected = sorted(
        {node.node_type for node in all_nodes if node.node_type not in allowed_types}
    )
    if unexpected:
        raise PipelineError(
            "invalid_structure_output",
            "pass emitted out-of-scope node types: " + ", ".join(unexpected),
        )
    if target is not None and target.kind in {"chapter", "part", "schedule"}:
        if len(draft.nodes) != 1:
            raise PipelineError(
                "incomplete_structure_output",
                f"{target.unit_id} must return exactly one root",
            )
        root = draft.nodes[0]
        if (
            root.node_type != target.kind
            or _normalized(root.display_label or "")
            != _normalized(target.display_label or "")
            or _normalized(root.heading or "") != _normalized(target.heading or "")
            or root.pdf_page != target.start_pdf_page
        ):
            raise PipelineError(
                "invalid_structure_output",
                f"{target.unit_id} did not repeat its planned root exactly",
            )

    def validate_node(
        node: Any,
        parent: Any | None,
        ancestor_texts: set[str],
        parent_path: tuple[str, ...],
        previous_sibling: Any | None,
    ) -> None:
        child_types = {child.node_type for child in node.children}
        if node.node_type == "section" and {"subsection", "paragraph"}.issubset(
            child_types
        ):
            raise PipelineError(
                "invalid_structure_output",
                "paragraphs must be nested under their applicable subsection",
            )
        allowed_parents = PARENT_NODE_TYPES.get(node.node_type)
        if parent is None and node.node_type in PARENT_REQUIRED_NODE_TYPES:
            raise PipelineError(
                "invalid_structure_output",
                f"{node.node_type} must have a structural parent",
            )
        if parent is not None and (
            allowed_parents is not None and parent.node_type not in allowed_parents
        ):
            raise PipelineError(
                "invalid_structure_output",
                f"{node.node_type} cannot be a child of {parent.node_type}",
            )
        if node.pdf_page > page_count or any(
            page > page_count
            for block in node.content_blocks
            for page in iter_block_pages(block)
        ):
            raise PipelineError(
                "invalid_structure_output", "structured output cites a nonexistent page"
            )
        if target is not None and (
            node.pdf_page < target.start_pdf_page
            or node.pdf_page > target.end_pdf_page
            or any(
                page < target.start_pdf_page or page > target.end_pdf_page
                for block in node.content_blocks
                for page in iter_block_pages(block)
            )
        ):
            raise PipelineError(
                "invalid_structure_output",
                f"{target.unit_id} cites a page outside its planned range",
            )
        if (
            not node.children
            and node.node_type not in CONTENT_OPTIONAL_LEAVES
            and not node.content_blocks
        ):
            raise PipelineError(
                "incomplete_structure_output",
                f"leaf {node.node_type} on PDF page {node.pdf_page} has no wording",
            )
        for field, value in (
            ("display label", node.display_label),
            ("heading", node.heading),
        ):
            if value and not _is_subsequence(
                _normalized(value), _normalized(_page_source(pages, [node.pdf_page]))
            ):
                raise PipelineError(
                    "source_text_mismatch",
                    f"{node.node_type} {field} {value!r} is not recoverable "
                    f"from PDF page {node.pdf_page}",
                )
        own_texts: set[str] = set()
        for block in node.content_blocks:
            if _hides_addressable_marker(block):
                raise PipelineError(
                    "incomplete_structure_output",
                    "addressable marker was hidden inside content_blocks",
                )
            if isinstance(block, DraftTableBlock):
                validate_table(block)
            for text, pdf_pages, description in iter_block_texts(block):
                normalized_text = _normalized(text)
                if normalized_text in ancestor_texts:
                    raise PipelineError(
                        "duplicate_structure_content",
                        "the same wording is owned by an ancestor and descendant",
                    )
                own_texts.add(normalized_text)
                if (
                    node.node_type in {"paragraph", "subparagraph"}
                    and node.display_label is None
                    and node.heading is None
                    and re.match(
                        r"\s*provided(?:\s+further)?\s+that\b", text, re.IGNORECASE
                    )
                ):
                    location = " > ".join(parent_path) or "the document root"
                    previous = (
                        f", immediately after {_node_reference(previous_sibling)}"
                        if previous_sibling is not None
                        else ""
                    )
                    excerpt = re.sub(r"\s+", " ", text).strip()[:160]
                    raise PipelineError(
                        "invalid_proviso_structure",
                        f"PDF page {node.pdf_page} has an unnumbered proviso node "
                        f"under {location}{previous}: {excerpt!r}. Remove the "
                        "anonymous node and place this wording in the "
                        "content_blocks of the provision it qualifies.",
                    )
                if not _is_subsequence(
                    normalized_text, _normalized(_page_source(pages, pdf_pages))
                ):
                    raise PipelineError(
                        "source_text_mismatch",
                        f"{description} is not recoverable from PDF pages {pdf_pages}",
                    )
        descendant_ancestors = ancestor_texts | own_texts
        node_path = (*parent_path, _node_reference(node))
        for index, child in enumerate(node.children):
            validate_node(
                child,
                node,
                descendant_ancestors,
                node_path,
                node.children[index - 1] if index else None,
            )

    for index, root in enumerate(draft.nodes):
        validate_node(
            root,
            None,
            set(),
            (),
            draft.nodes[index - 1] if index else None,
        )


def _validate_section_sequence(provisions: list[dict[str, Any]]) -> None:
    previous: tuple[int, str] | None = None
    for provision in provisions:
        if provision["node_type"] != "section" or not provision["display_label"]:
            continue
        match = re.fullmatch(
            r"\s*(\d+)([a-z]?)\.?\s*",
            provision["display_label"],
            re.IGNORECASE,
        )
        if match is None:
            previous = None
            continue
        current = (int(match.group(1)), match.group(2).casefold())
        if previous is None:
            valid = True
        else:
            previous_number, previous_suffix = previous
            current_number, current_suffix = current
            next_inserted_suffix = (
                chr(ord(previous_suffix) + 1) if previous_suffix else "a"
            )
            valid = (
                current_number == previous_number
                and current_suffix == next_inserted_suffix
            ) or (current_number == previous_number + 1 and current_suffix == "")
        if not valid:
            assert previous is not None
            raise PipelineError(
                "incomplete_structure_output",
                "section sequence jumps from "
                f"{previous[0]}{previous[1].upper()} to "
                f"{current[0]}{current[1].upper()}",
            )
        previous = current


def _work_key(source_id: str, settings: StructureSettings) -> str:
    material = "|".join(
        (
            source_id,
            str(STRUCTURE_VERSION),
            str(PROMPT_VERSION),
            settings.base_url,
            settings.primary_model,
        )
    )
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]
    return f"{source_id.removeprefix('sha256:')[:12]}-{digest}"


def _validate_plan(plan: StructurePlan, pages: list[dict[str, Any]]) -> None:
    page_count = len(pages)
    if len(plan.units) > 64:
        raise PipelineError(
            "invalid_structure_plan", "plan exceeds the 64-unit execution limit"
        )
    if plan.legal_end_pdf_page > page_count:
        raise PipelineError(
            "invalid_structure_plan", "legal range cites a nonexistent PDF page"
        )
    for previous, unit in zip(plan.units, plan.units[1:], strict=False):
        if unit.start_pdf_page != previous.end_pdf_page + 1:
            continue
        page_source = _page_source(pages, [unit.start_pdf_page])
        lines = page_source.splitlines()
        identifier_line = next(
            (
                index
                for index, line in enumerate(lines)
                if any(
                    value and _normalized(value) in _normalized(line)
                    for value in (unit.display_label, unit.heading)
                )
            ),
            None,
        )
        if identifier_line is not None and PLAN_SCOPE_MARKER.search(
            "\n".join(lines[:identifier_line])
        ):
            # Both workers need the physical page when the prior root continues
            # above the next root's heading.
            previous.end_pdf_page = unit.start_pdf_page
    issues: list[str] = []
    for page in pages:
        if not (
            plan.legal_start_pdf_page <= page["pdf_page"] <= plan.legal_end_pdf_page
        ):
            continue
        excluded_heading = EXCLUDED_PLAN_HEADING.search(page["text"])
        if excluded_heading:
            issues.append(
                f"planned legal range includes excluded "
                f"{excluded_heading.group(1).casefold()} on PDF page "
                f"{page['pdf_page']}"
            )
    outside_markers = [
        page["pdf_page"]
        for page in pages
        if not (
            plan.legal_start_pdf_page <= page["pdf_page"] <= plan.legal_end_pdf_page
        )
        and PLAN_SCOPE_MARKER.search(page["text"])
    ]
    if outside_markers:
        issues.append(
            "addressable legal markers fall outside the planned legal range on PDF "
            f"pages {outside_markers[:10]}"
        )
    first_unit = plan.units[0]
    last_unit = plan.units[-1]
    if plan.legal_start_pdf_page != first_unit.start_pdf_page:
        issues.append(
            f"legal_start_pdf_page {plan.legal_start_pdf_page} does not match first "
            f"unit start_pdf_page {first_unit.start_pdf_page}"
        )
    if plan.legal_end_pdf_page != last_unit.end_pdf_page:
        issues.append(
            f"legal_end_pdf_page {plan.legal_end_pdf_page} does not match last unit "
            f"end_pdf_page {last_unit.end_pdf_page}"
        )
    identities: set[str] = set()
    previous: StructureUnit | None = None
    covered: set[int] = set()
    for unit in plan.units:
        if unit.unit_id in identities:
            issues.append(f"duplicate unit_id: {unit.unit_id}")
        if (
            unit.start_pdf_page < plan.legal_start_pdf_page
            or unit.end_pdf_page > plan.legal_end_pdf_page
        ):
            issues.append(f"{unit.unit_id} falls outside the legal page range")
        if previous is not None:
            if unit.start_pdf_page < previous.end_pdf_page:
                issues.append(
                    "units overlap by more than their shared boundary page",
                )
            if unit.start_pdf_page > previous.end_pdf_page + 1:
                issues.append(
                    f"page gap between {previous.unit_id} and {unit.unit_id}",
                )
        page_source = _page_source(pages, [unit.start_pdf_page])
        for field, value in (
            ("display_label", unit.display_label),
            ("heading", unit.heading),
        ):
            if value and not _is_subsequence(
                _normalized(value), _normalized(page_source)
            ):
                issues.append(
                    f"{unit.unit_id} {field} {value!r} is not recoverable from "
                    f"starting PDF page {unit.start_pdf_page}; use exact printed "
                    "text or null"
                )
        identities.add(unit.unit_id)
        covered.update(range(unit.start_pdf_page, unit.end_pdf_page + 1))
        previous = unit
    expected = set(range(plan.legal_start_pdf_page, plan.legal_end_pdf_page + 1))
    if covered != expected:
        missing = sorted(expected - covered)
        issues.append(f"planned units do not cover legal pages: {missing[:10]}")
    if issues:
        detail = "; ".join(issues[:20])
        if len(issues) > 20:
            detail += f"; and {len(issues) - 20} more issue(s)"
        raise PipelineError(
            "invalid_structure_plan",
            detail,
        )


def _plan_scope(previous_plan: StructurePlan | None, audit: Any | None) -> str:
    if previous_plan is None:
        return "Plan the complete document according to the planning instructions."
    audit_issues = [] if audit is None else audit.issues[:100]
    return (
        "The previous plan produced audit failures. Return a complete replacement "
        "plan with corrected legal scope or semantic boundaries.\n"
        f"PREVIOUS PLAN\n{previous_plan.model_dump_json()}\n"
        f"AUDIT SUMMARY\n{json.dumps([issue.model_dump(mode='json') for issue in audit_issues], ensure_ascii=False)}"
    )


def _unit_scope(
    unit: StructureUnit,
    issues: list[Any],
    previous_draft: StructureDraft | None,
) -> str:
    identity = json.dumps(
        unit.display_label or unit.heading or unit.kind, ensure_ascii=False
    )
    if unit.kind == "front_matter":
        target = (
            "Return only the complete front matter in this page range: document "
            "title, long title, preamble, and enacting formula when printed."
        )
    elif unit.kind == "body":
        target = "Return the complete unparted operative body in this page range."
    else:
        target = (
            f"Return only the complete {unit.kind} {identity} as the sole top-level "
            "node, with every descendant and all directly owned wording. Repeat "
            "its planned label, heading, and starting PDF page exactly."
        )
    scope = (
        f"{target} The owned source is PDF pages {unit.start_pdf_page} through "
        f"{unit.end_pdf_page}. Do not emit adjacent roots that merely share a "
        "boundary page. Exclude running page furniture and bracketed editorial "
        "cross-references or alteration notes."
    )
    if previous_draft is not None:
        scope += (
            "\n\nThis is a fresh full-replacement repair pass. Correct every audit "
            "issue; do not return a patch and do not preserve an error merely "
            "because it appeared in the prior draft.\nAUDIT ISSUES\n"
            + json.dumps(
                [issue.model_dump(mode="json") for issue in issues[:100]],
                ensure_ascii=False,
            )
            + "\nPREVIOUS DRAFT\n"
            + previous_draft.model_dump_json()
        )
    return scope


@contextmanager
def _structure_lock(cache_root: Path, work_key: str) -> Iterator[None]:
    lock_path = cache_root / "structure-work" / work_key / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PipelineError(
                "structure_run_locked",
                f"another structure run is active for {work_key}",
                retryable=True,
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def structure(
    extraction_path: Path,
    *,
    execute: bool = False,
    cache_root: Path,
    settings: StructureSettings | None = None,
    primary_runner: ModelRunner = _run_primary,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    extraction, relative_input = _load_extraction(extraction_path, cache_root)
    raw_text = build_raw_text(extraction["pages"])
    if not execute:
        return {
            "stage": "structure",
            "status": "dry_run",
            "network_access": False,
            "input_extraction": relative_input.as_posix(),
            "source_id": extraction["source_id"],
            "models": {"primary": DEFAULT_PRIMARY_MODEL},
            "request": {
                "strategy": "agent_planned_parallel_units_with_audit_repair",
                "planner_receives_full_text": True,
                "workers_receive_bounded_page_ranges": True,
                "checkpoint_resume": True,
                "deterministic_finish_gate": True,
                "pdf_pages": [1, extraction["page_count"]],
                "text_characters": len(raw_text),
            },
        }

    active_settings = settings or StructureSettings.from_env()
    started_at = utc_now()
    work_key = _work_key(extraction["source_id"], active_settings)
    if progress is not None:
        progress(
            {
                "event": "structure_started",
                "source_id": extraction["source_id"],
                "pdf_pages": extraction["page_count"],
                "text_characters": len(raw_text),
                "model": active_settings.primary_model,
                "concurrency": active_settings.concurrency,
                "max_repair_rounds": active_settings.max_repair_rounds,
                "work_key": work_key,
            }
        )

    def pages_text(start_page: int, end_page: int) -> str:
        return build_raw_text(
            [
                page
                for page in extraction["pages"]
                if start_page <= page["pdf_page"] <= end_page
            ]
        )

    deps = StructureDeps(
        cache_root=cache_root,
        work_key=work_key,
        source_id=extraction["source_id"],
        pages=extraction["pages"],
        page_count=extraction["page_count"],
        settings=active_settings,
        runner=primary_runner,
        plan_scope=_plan_scope,
        unit_scope=_unit_scope,
        pages_text=pages_text,
        validate_plan=lambda plan: _validate_plan(plan, extraction["pages"]),
        validate_unit=lambda draft, unit: _validate_draft(
            draft,
            allowed_types=UNIT_NODE_TYPES[unit.kind],
            page_count=extraction["page_count"],
            pages=extraction["pages"],
            target=unit,
        ),
        structure_version=STRUCTURE_VERSION,
        prompt_version=PROMPT_VERSION,
        progress=progress,
    )

    async def execute_workflow():
        if primary_runner is not _run_primary:
            return await run_structure_graph(deps)
        client = _client(active_settings)
        try:
            shared_model = _model(
                active_settings.primary_model,
                active_settings,
                client=client,
            )

            async def shared_runner(
                source_text: str,
                runner_settings: StructureSettings,
                scope: str,
                output_type: type[BaseModel],
                validate: Any = None,
            ) -> ModelRun:
                return await _run_agent(
                    source_text,
                    runner_settings,
                    scope,
                    output_type,
                    validate,
                    model=shared_model,
                )

            deps.runner = shared_runner
            return await run_structure_graph(deps)
        finally:
            await client.close()

    with _structure_lock(cache_root, work_key):
        workflow = asyncio.run(execute_workflow())
        provisions, content_characters = materialize_provisions(
            workflow.drafts, source_id=extraction["source_id"]
        )
        if not provisions:
            raise PipelineError(
                "incomplete_structure_output", "model emitted no Provision drafts"
            )
        _validate_section_sequence(provisions)
        final_audit = audit_materialized_provisions(
            provisions,
            pages=extraction["pages"],
            legal_start_pdf_page=workflow.plan.legal_start_pdf_page,
            legal_end_pdf_page=workflow.plan.legal_end_pdf_page,
        )
        if progress is not None:
            progress(
                {
                    "event": "materialized_audit_completed",
                    "passed": final_audit.passed,
                    "issues": len(final_audit.issues),
                    "claimed_characters": final_audit.claimed_characters,
                    "source_characters": final_audit.source_characters,
                    "claimed_markers": final_audit.claimed_markers,
                    "source_markers": final_audit.source_markers,
                }
            )
        if not final_audit.passed:
            audit_path = Path("structure-work") / work_key / "materialized-audit.json"
            write_json_result(
                cache_root,
                {
                    "stage": "structure_audit",
                    "status": "failure",
                    "source_id": extraction["source_id"],
                    "report": final_audit.model_dump(mode="json"),
                },
                audit_path,
            )
            raise PipelineError(
                "structure_audit_failed",
                f"materialized structure failed {len(final_audit.issues)} audit "
                f"checks; audit: {audit_path.as_posix()}",
            )

        pass_records = workflow.pass_records
        usage = {
            key: sum(record["usage"].get(key, 0) for record in pass_records)
            for key in ("requests", "input_tokens", "output_tokens")
        }
        model_run = {
            "model": active_settings.primary_model,
            "output_mode": "tool",
            "streaming": False,
            "latency_seconds": round(
                sum(record["latency_seconds"] for record in pass_records), 3
            ),
            "usage": usage,
            "passes": pass_records,
        }
        summary = {
            "passes": len(pass_records),
            "units": len(workflow.plan.units),
            "repair_rounds": workflow.repair_rounds,
            "checkpoints_reused": sum(
                record["checkpoint_reused"] for record in pass_records
            ),
            "provisions": len(provisions),
            "content_characters": content_characters,
            "source_characters": final_audit.source_characters,
            "claimed_source_characters": final_audit.claimed_characters,
            "source_markers": final_audit.source_markers,
            "claimed_source_markers": final_audit.claimed_markers,
        }
        artifact = {
            "stage": "structure",
            "status": "success",
            "structure_version": STRUCTURE_VERSION,
            "prompt_version": PROMPT_VERSION,
            "started_at": iso_timestamp(started_at),
            "finished_at": iso_timestamp(utc_now()),
            "network_access": True,
            "input_extraction": relative_input.as_posix(),
            "source_id": extraction["source_id"],
            "page_count": extraction["page_count"],
            "plan": workflow.plan.model_dump(mode="json"),
            "audit": final_audit.model_dump(mode="json"),
            "model_run": model_run,
            "provisions": provisions,
            "summary": summary,
        }
        digest = extraction["source_id"].removeprefix("sha256:")
        run_id = f"{started_at:%Y%m%dT%H%M%SZ}-{digest[:8]}-{uuid.uuid4().hex[:8]}"
        write_json_result(cache_root, artifact, Path("structures") / f"{run_id}.json")
        if progress is not None:
            progress(
                {
                    "event": "structure_completed",
                    "result_path": artifact["result_path"],
                    "summary": summary,
                }
            )
        return {
            "stage": artifact["stage"],
            "status": artifact["status"],
            "source_id": artifact["source_id"],
            "summary": summary,
            "result_path": artifact["result_path"],
        }
