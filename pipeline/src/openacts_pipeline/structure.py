"""Turn extracted PDF text into complete, source-backed Provision drafts."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, ToolOutput
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
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
from openacts_pipeline.structure_schema import (
    DraftListBlock,
    DraftTableBlock,
    DraftTextBlock,
    FocusPlan,
    FocusUnit,
    StructureDraft,
    iter_block_pages,
    iter_block_texts,
    materialize_provisions,
    validate_table,
)

STRUCTURE_VERSION = 3
SUPPORTED_EXTRACTION_VERSIONS = {1, 2}
PROMPT_VERSION = 15
MAX_OUTPUT_TOKENS = 384_000
TRANSIENT_MODEL_STATUSES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ModelRun:
    output: BaseModel
    model: str
    output_mode: str
    latency_seconds: float
    usage: dict[str, int]


ModelRunner = Callable[
    [
        str,
        StructureSettings,
        str,
        type[BaseModel],
        Callable[[BaseModel], None] | None,
    ],
    ModelRun,
]


SYSTEM_INSTRUCTIONS = """Transform the full raw text of one Nigerian legislative
PDF into complete structured legal data. This is reconstruction and structural
extraction, not summarisation, interpretation, or metadata-only outlining.

INPUT AND FIDELITY
The user supplies the entire raw PDF text. Each page starts with a marker such
as `--- PDF PAGE 7 ---`. Reconstruct the printed legal reading order from text
that may have line wraps, split words, marginal headings after body text,
multi-column extraction, and repeated running matter. Remove page furniture,
running headers, footers, and printed page numbers. Repair only mechanical
extraction artefacts such as `DA TA`, `f )`, or a word split at a line break.
Never silently modernise, paraphrase, complete, or legally reinterpret wording.

COMPLETE WORDING
Return the complete wording of every requested provision. Never summarise,
shorten, omit, or replace wording with a heading. A node's content_blocks contain
only wording directly owned by that node: exclude its display label, heading,
and all wording owned by child nodes. Introductory wording before child nodes
belongs to the parent; each child contains its own wording. Containers may have
no content. Every leaf that visibly contains wording must return that wording.

STRUCTURE
Use only these canonical node types: document_title, long_title, arrangement,
preamble, enacting_formula, part, chapter, division, cross_heading, section,
subsection, paragraph, subparagraph, definition, schedule, schedule_part,
schedule_paragraph, schedule_subparagraph, table, form, authentication, and
explanatory_note. Do not emit arrangement or table-of-contents entries; derive
navigation from the operative text. Preserve visible labels and headings
separately. Return nodes as a tree in legal reading order: each node's direct
descendants go in its `children` array and only document-level roots go in the
top-level `nodes` array.

Every printed structural marker creates its own node. In particular, each `(1)`,
`(2)`, `(a)`, `(b)`, `(i)`, and similar addressable marker must become a
subsection, paragraph, or subparagraph node with the marker in display_label.
Never leave such a marker at the start of a text block or turn addressable legal
paragraphs into list items. Given `PART I 1.—(1) Intro — (a) First`, return one
top-level Part whose children contain Section 1, whose children contain
Subsection (1), whose children contain Paragraph (a). The section has no text;
the subsection owns `Intro —`; the paragraph owns `First`:
`{"nodes":[{"node_type":"part","display_label":"PART I","pdf_page":1,
"children":[{"node_type":"section","display_label":"1.","pdf_page":1,
"children":[{"node_type":"subsection","display_label":"(1)","pdf_page":1,
"content_blocks":[{"kind":"text","text":"Intro —","pdf_pages":[1]}],
"children":[{"node_type":"paragraph","display_label":"(a)","pdf_page":1,
"content_blocks":[{"kind":"text","text":"First","pdf_pages":[1]}]}]}]}]}]}`.
When a section has subsection children, its paragraphs must be children of the
applicable subsection, never siblings of it.

Definitions may also own marked paragraphs. Given `"competent authority"
includes — (a) First; or (b) Second;`, return one definition whose own text is
only `"competent authority" includes —` and whose children are Paragraph (a)
and Paragraph (b), each owning its complete wording. Never flatten those
markers and their wording into the definition's text block.

Within each Schedule, use this hierarchy when the printed levels exist:
schedule -> optional schedule_part -> schedule_paragraph ->
schedule_subparagraph -> paragraph -> subparagraph. The first parenthesised
subdivision beneath a numbered schedule paragraph is a schedule_subparagraph
whether its marker is `(1)` or `(a)`. Preserve the printed Schedule label, such
as `SCHEDULE`, `FIRST SCHEDULE`, or `SECOND SCHEDULE`, exactly in display_label.

Do not copy a child's introductory wording into its parent. Keep an unnumbered
proviso beginning `Provided that` inside the existing containing node's
content_blocks array, for example
`{"kind":"text","text":"Provided that ...","pdf_pages":[8]}`. Never append it
to the nodes array and never invent a `node_type` named `text`. A proviso with a
printed structural marker uses the actual paragraph or subparagraph node.

`display_label` contains only a printed marker such as `PART II`, `25.`, `(2)`,
or `(a)`. `heading` contains only its descriptive marginal or inline heading.
Put long-title wording, enacting wording, signatures, authentication wording,
and explanatory wording in content_blocks rather than disguising them as
headings. Use the operative occurrence once; ignore duplicate cover titles,
Gazette furniture, and entries that appear only in an arrangement.

CONTENT BLOCKS
Use text, quoted_text, formula, or signature blocks for continuous wording. Use
a list block only for an unaddressable printed list such as bullets; decimal,
alphabetic, and Roman legal markers are always Provision nodes. Use a table
block for a logical grid rather than flattening it. Set column_count,
header_row_count, and rows as a rectangular matrix in printed reading order.
Each matrix cell is its complete text, or null for a printed blank. Do not emit
cell IDs, roles, spans, or header references; the pipeline derives those. The
node pdf_page is where its own label, heading, or wording begins.
caption_pdf_pages and every other pdf_pages array cite the pages containing
that exact material.

Before returning, confirm that the requested unit is complete, every word is
owned exactly once, every node is nested under its direct parent, repeated
labels under different parents remain distinct, tables remain tables, and all
page evidence exists in the supplied document."""

OUTLINE_SCOPE = """Discover every operative Part and Schedule in the document.
Return only FocusPlan units in legal reading order. Do not return sections,
content blocks, arrangement entries, front matter, authentication, explanatory
matter, or tables. If the operative body has no Parts, return no Part units."""

FRONT_SCOPE = """Return only the complete front matter before the operative body,
including the document title, long title, preamble, and enacting formula when
printed. Return complete wording in content_blocks. Do not return arrangement
entries, Parts, sections, Schedules, authentication, or back matter. Return an
empty nodes array when no front matter exists."""

BODY_SCOPE = """Return only the complete operative body and every descendant.
The discovery pass found no Part containers. Include all exact wording. Do not
return front matter, Schedules, authentication, explanatory matter, or
arrangement entries."""

FRONT_NODE_TYPES = {
    "document_title",
    "long_title",
    "preamble",
    "enacting_formula",
}
BODY_NODE_TYPES = {
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
}
PART_NODE_TYPES = BODY_NODE_TYPES | {"part"}
SCHEDULE_NODE_TYPES = {
    "schedule",
    "schedule_part",
    "schedule_paragraph",
    "schedule_subparagraph",
    "paragraph",
    "subparagraph",
    "cross_heading",
    "table",
    "form",
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
STRUCTURAL_LIST_STYLES = {
    "decimal",
    "lower_alpha",
    "upper_alpha",
    "lower_roman",
    "upper_roman",
}
PARENT_NODE_TYPES = {
    "section": {"part", "chapter", "division"},
    "subsection": {"section"},
    "paragraph": {"section", "subsection", "definition", "schedule_subparagraph"},
    "subparagraph": {"paragraph"},
    "schedule_part": {"schedule"},
    "schedule_paragraph": {"schedule", "schedule_part"},
    "schedule_subparagraph": {"schedule_paragraph"},
}
PARENT_REQUIRED_NODE_TYPES = set(PARENT_NODE_TYPES) - {"section"}

DEEPSEEK_PROFILE = cast(
    OpenAIModelProfile,
    {
        "supports_tools": True,
        "supports_thinking": True,
        "openai_chat_thinking_field": "reasoning_content",
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


def _model(name: str, settings: StructureSettings) -> OpenAIChatModel:
    client = AsyncOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        max_retries=0,
        timeout=settings.request_timeout_seconds,
    )
    return OpenAIChatModel(
        name,
        provider=OpenAIProvider(openai_client=client),
        profile=DEEPSEEK_PROFILE,
    )


def _usage(result: Any) -> dict[str, int]:
    usage = result.usage
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def _run_agent(
    raw_text: str,
    settings: StructureSettings,
    scope: str,
    output_type: type[BaseModel],
    validate: Callable[[BaseModel], None] | None = None,
) -> ModelRun:
    agent = Agent(
        _model(settings.primary_model, settings),
        output_type=ToolOutput(output_type, strict=False),
        instructions=f"{SYSTEM_INSTRUCTIONS}\n\nREQUEST SCOPE\n{scope}",
        retries=2,
        model_settings={
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )
    if validate is not None:

        @agent.output_validator
        def validate_source(output: BaseModel) -> BaseModel:
            try:
                validate(output)
            except PipelineError as exc:
                raise ModelRetry(f"{exc.code}: {exc}") from exc
            return output

    started = time.perf_counter()
    try:
        result = agent.run_sync(raw_text)
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
    except UnexpectedModelBehavior as exc:
        raise PipelineError(
            "model_invalid_output",
            f"{settings.primary_model} did not return valid structured output",
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


def _run_primary(
    raw_text: str,
    settings: StructureSettings,
    scope: str,
    output_type: type[BaseModel],
    validate: Callable[[BaseModel], None] | None = None,
) -> ModelRun:
    return _run_agent(raw_text, settings, scope, output_type, validate)


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
        return block.marker_style in STRUCTURAL_LIST_STYLES or any(
            _hides_addressable_marker(child)
            for item in block.items
            for child in item.content_blocks
        )
    return False


def _walk_nodes(nodes: Iterable[Any]) -> Iterable[Any]:
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)


def _validate_focus_plan(plan: FocusPlan, *, page_count: int) -> None:
    seen: set[tuple[object, ...]] = set()
    previous_page = 0
    for unit in plan.units:
        if unit.pdf_page > page_count:
            raise PipelineError(
                "invalid_structure_output", "focus unit cites a nonexistent PDF page"
            )
        identity = (
            unit.kind,
            _normalized(unit.display_label or ""),
            _normalized(unit.heading or ""),
            unit.pdf_page,
        )
        if identity in seen:
            raise PipelineError(
                "invalid_structure_output", "focus plan contains a duplicate unit"
            )
        if unit.pdf_page < previous_page:
            raise PipelineError(
                "invalid_structure_output", "focus units are not in legal order"
            )
        seen.add(identity)
        previous_page = unit.pdf_page


def _validate_draft(
    draft: StructureDraft,
    *,
    allowed_types: set[str],
    page_count: int,
    pages: list[dict[str, Any]],
    target: FocusUnit | None = None,
) -> None:
    all_nodes = list(_walk_nodes(draft.nodes))
    unexpected = sorted(
        {node.node_type for node in all_nodes if node.node_type not in allowed_types}
    )
    if unexpected:
        raise PipelineError(
            "invalid_structure_output",
            "pass emitted out-of-scope node types: " + ", ".join(unexpected),
        )
    if target is not None:
        if len(draft.nodes) != 1:
            raise PipelineError(
                "incomplete_structure_output",
                f"{target.kind} pass must return exactly one root",
            )
        first = draft.nodes[0]
        if (
            first.node_type != target.kind
            or _normalized(first.display_label or "")
            != _normalized(target.display_label or "")
            or _normalized(first.heading or "") != _normalized(target.heading or "")
            or first.pdf_page != target.pdf_page
        ):
            raise PipelineError(
                "invalid_structure_output",
                f"{target.kind} pass did not repeat its exact root first",
            )

    def validate_node(
        node: Any,
        parent: Any | None,
        ancestor_texts: set[str],
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
                    raise PipelineError(
                        "invalid_proviso_structure",
                        "an unnumbered proviso must remain a content block",
                    )
                if not _is_subsequence(
                    normalized_text, _normalized(_page_source(pages, pdf_pages))
                ):
                    raise PipelineError(
                        "source_text_mismatch",
                        f"{description} is not recoverable from PDF pages {pdf_pages}",
                    )
        descendant_ancestors = ancestor_texts | own_texts
        for child in node.children:
            validate_node(child, node, descendant_ancestors)

    for root in draft.nodes:
        validate_node(root, None, set())


def _validate_section_sequence(provisions: list[dict[str, Any]]) -> None:
    previous: int | None = None
    for provision in provisions:
        if provision["node_type"] != "section" or not provision["display_label"]:
            continue
        match = re.fullmatch(r"\s*(\d+)\.?\s*", provision["display_label"])
        if match is None:
            previous = None
            continue
        current = int(match.group(1))
        if previous is not None and current != previous + 1:
            raise PipelineError(
                "incomplete_structure_output",
                f"section sequence jumps from {previous} to {current}",
            )
        previous = current


def _target_scope(target: FocusUnit) -> str:
    label = json.dumps(target.display_label or target.heading, ensure_ascii=False)
    heading = (
        f" with heading {json.dumps(target.heading, ensure_ascii=False)}"
        if target.display_label and target.heading
        else ""
    )
    descendants = (
        "every descendant and all wording directly owned by each node"
        if target.kind == "part"
        else "every Schedule subdivision and all wording directly owned by each node"
    )
    return (
        f"Return only the complete operative {target.kind} {label}{heading}, beginning "
        f"on PDF page {target.pdf_page}, {descendants}. Return that root as the "
        "only top-level node, with the exact label, heading, and PDF page. Nest "
        "every descendant under its direct parent. Do not return front "
        "matter, other Parts or Schedules, authentication, explanatory matter, "
        "arrangement entries, or running matter."
    )


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


def _run_checkpointed(
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
) -> tuple[ModelRun, bool]:
    relative_path = (
        Path("structure-work") / work_key / f"{pass_index:03d}-{pass_name}.json"
    )
    path = cache_root / relative_path
    scope_hash = hashlib.sha256(scope.encode()).hexdigest()
    if path.exists():
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            if (
                checkpoint["stage"] != "structure_pass"
                or checkpoint["structure_version"] != STRUCTURE_VERSION
                or checkpoint["prompt_version"] != PROMPT_VERSION
                or checkpoint["source_id"] != source_id
                or checkpoint["configured_model"] != settings.primary_model
                or checkpoint["pass_index"] != pass_index
                or checkpoint["pass_name"] != pass_name
                or checkpoint["scope_sha256"] != scope_hash
                or checkpoint["output_type"] != output_type.__name__
            ):
                raise ValueError("checkpoint metadata does not match this pass")
            output = output_type.model_validate(checkpoint["output"])
            run_data = checkpoint["model_run"]
            run = ModelRun(
                output=output,
                model=run_data["model"],
                output_mode=run_data["output_mode"],
                latency_seconds=run_data["latency_seconds"],
                usage=run_data["usage"],
            )
            validate(output)
            return run, True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PipelineError(
                "invalid_structure_checkpoint", f"cannot resume {pass_name}: {exc}"
            ) from exc

    run = runner(raw_text, settings, scope, output_type, validate)
    if not isinstance(run.output, output_type):
        raise PipelineError(
            "model_invalid_output", f"{pass_name} used the wrong schema"
        )
    validate(run.output)
    checkpoint = {
        "stage": "structure_pass",
        "status": "success",
        "structure_version": STRUCTURE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "source_id": source_id,
        "configured_model": settings.primary_model,
        "pass_index": pass_index,
        "pass_name": pass_name,
        "scope_sha256": scope_hash,
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


def _pass_record(
    pass_name: str,
    run: ModelRun,
    reused: bool,
    target: FocusUnit | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "pass": pass_name,
        "model": run.model,
        "output_mode": run.output_mode,
        "checkpoint_reused": reused,
        "latency_seconds": run.latency_seconds,
        "usage": run.usage,
    }
    if target is not None:
        record["target"] = target.model_dump(mode="json")
    return record


def structure(
    extraction_path: Path,
    *,
    execute: bool = False,
    cache_root: Path,
    settings: StructureSettings | None = None,
    primary_runner: ModelRunner = _run_primary,
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
                "strategy": "discovery_then_complete_units",
                "each_pass_receives_full_text": True,
                "checkpoint_resume": True,
                "pdf_pages": [1, extraction["page_count"]],
                "text_characters": len(raw_text),
            },
        }

    active_settings = settings or StructureSettings.from_env()
    started_at = utc_now()
    work_key = _work_key(extraction["source_id"], active_settings)
    pass_index = 0
    pass_records: list[dict[str, Any]] = []
    drafts: list[StructureDraft] = []

    outline_run, reused = _run_checkpointed(
        cache_root=cache_root,
        work_key=work_key,
        pass_index=pass_index,
        pass_name="outline",
        source_id=extraction["source_id"],
        raw_text=raw_text,
        settings=active_settings,
        scope=OUTLINE_SCOPE,
        output_type=FocusPlan,
        runner=primary_runner,
        validate=lambda output: _validate_focus_plan(
            cast(FocusPlan, output), page_count=extraction["page_count"]
        ),
    )
    plan = cast(FocusPlan, outline_run.output)
    pass_records.append(_pass_record("outline", outline_run, reused))

    def run_draft(
        pass_name: str,
        scope: str,
        allowed_types: set[str],
        target: FocusUnit | None = None,
    ) -> None:
        nonlocal pass_index
        pass_index += 1

        def validate_output(output: BaseModel) -> None:
            draft = StructureDraft.model_validate(output.model_dump(mode="json"))
            _validate_draft(
                draft,
                allowed_types=allowed_types,
                page_count=extraction["page_count"],
                pages=extraction["pages"],
                target=target,
            )

        run, checkpoint_reused = _run_checkpointed(
            cache_root=cache_root,
            work_key=work_key,
            pass_index=pass_index,
            pass_name=pass_name,
            source_id=extraction["source_id"],
            raw_text=raw_text,
            settings=active_settings,
            scope=scope,
            output_type=StructureDraft,
            runner=primary_runner,
            validate=validate_output,
        )
        drafts.append(StructureDraft.model_validate(run.output.model_dump(mode="json")))
        pass_records.append(_pass_record(pass_name, run, checkpoint_reused, target))

    run_draft("front-matter", FRONT_SCOPE, FRONT_NODE_TYPES)
    parts = [unit for unit in plan.units if unit.kind == "part"]
    schedules = [unit for unit in plan.units if unit.kind == "schedule"]
    if parts:
        for number, target in enumerate(parts, start=1):
            run_draft(
                f"part-{number:02d}",
                _target_scope(target),
                PART_NODE_TYPES,
                target,
            )
    else:
        run_draft("operative-body", BODY_SCOPE, BODY_NODE_TYPES)
    for number, target in enumerate(schedules, start=1):
        run_draft(
            f"schedule-{number:02d}",
            _target_scope(target),
            SCHEDULE_NODE_TYPES,
            target,
        )

    provisions, content_characters = materialize_provisions(
        drafts, source_id=extraction["source_id"]
    )
    if not provisions:
        raise PipelineError(
            "incomplete_structure_output", "model emitted no Provision drafts"
        )
    _validate_section_sequence(provisions)

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
        "checkpoints_reused": sum(
            record["checkpoint_reused"] for record in pass_records
        ),
        "provisions": len(provisions),
        "content_characters": content_characters,
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
        "model_run": model_run,
        "provisions": provisions,
        "summary": summary,
    }
    digest = extraction["source_id"].removeprefix("sha256:")
    run_id = f"{started_at:%Y%m%dT%H%M%SZ}-{digest[:8]}-{uuid.uuid4().hex[:8]}"
    write_json_result(cache_root, artifact, Path("structures") / f"{run_id}.json")
    return {
        "stage": artifact["stage"],
        "status": artifact["status"],
        "source_id": artifact["source_id"],
        "summary": summary,
        "result_path": artifact["result_path"],
    }
