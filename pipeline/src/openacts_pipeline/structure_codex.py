"""Structured model passes executed through the local codex CLI."""

import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import DEFAULT_CODEX_MODEL_LABEL, StructureSettings
from openacts_pipeline.structure_prompts import (
    CRITIC_INSTRUCTIONS,
    PLAN_INSTRUCTIONS,
    SYSTEM_INSTRUCTIONS,
)
from openacts_pipeline.structure_runtime import ModelRun, RejectedModelOutput
from openacts_pipeline.structure_schema import RepairPatch, RepairPlan, StructurePlan

CODEX_BINARY = "codex"
# codex sends --output-schema as an OpenAI strict response format. It rejects
# these keywords outright, so they are restated in `description` instead:
# dropping minItems silently invites an empty list that then fails validation.
# Pydantic still checks the parsed reply, so the wire schema costs no enforcement.
CONSTRAINT_KEYWORDS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "uniqueItems",
)
UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({"default", *CONSTRAINT_KEYWORDS})
SCHEMA_ANNOTATION_KEYWORDS = frozenset(
    {
        "$comment",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
)


def strict_schema(node: Any) -> Any:
    """Rewrite a Pydantic schema into the strict subset codex accepts."""
    if isinstance(node, list):
        return [strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned = {
        key: strict_schema(value)
        for key, value in node.items()
        if key not in UNSUPPORTED_SCHEMA_KEYWORDS
    }
    dropped = [f"{key}={node[key]}" for key in CONSTRAINT_KEYWORDS if key in node]
    if dropped:
        stated = "Constraints: " + ", ".join(dropped) + "."
        existing = cleaned.get("description")
        cleaned["description"] = f"{existing} {stated}" if existing else stated
    if "oneOf" in cleaned:
        # Strict mode permits anyOf but not oneOf, which Pydantic emits for the
        # content-block union.
        cleaned["anyOf"] = cleaned.pop("oneOf")
    properties = cleaned.get("properties")
    if isinstance(properties, dict):
        cleaned["required"] = list(properties)
        cleaned["additionalProperties"] = False
    elif all(key in SCHEMA_ANNOTATION_KEYWORDS for key in cleaned):
        # Strict mode cannot express arbitrary JSON, so an Any-typed field travels
        # as JSON text and is decoded after the reply parses. It stays nullable
        # because every property is required, and a field that is legitimately
        # absent must be able to send null.
        cleaned["anyOf"] = [{"type": "string"}, {"type": "null"}]
    return cleaned


def _instructions(output_type: type[BaseModel]) -> str:
    if output_type is StructurePlan:
        return PLAN_INSTRUCTIONS
    if output_type is RepairPlan:
        return CRITIC_INSTRUCTIONS
    return SYSTEM_INSTRUCTIONS


def _command(
    settings: StructureSettings, schema_path: Path, message_path: Path
) -> list[str]:
    command = [
        CODEX_BINARY,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(message_path),
        "-c",
        f"model_reasoning_effort={settings.codex_reasoning_effort}",
    ]
    if settings.primary_model and settings.primary_model != DEFAULT_CODEX_MODEL_LABEL:
        command += ["--model", settings.primary_model]
    # A bare "-" reads the prompt from stdin; passing it as argv would leave stdin
    # open and codex would block waiting for a second input block.
    command.append("-")
    return command


def _events(events: str) -> Iterator[dict[str, Any]]:
    for line in events.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield parsed


def _usage(events: str) -> dict[str, int]:
    usage = {"requests": 1, "input_tokens": 0, "output_tokens": 0}
    for event in _events(events):
        if event.get("type") != "turn.completed":
            continue
        reported = event.get("usage") or {}
        usage["input_tokens"] += int(reported.get("input_tokens", 0))
        usage["output_tokens"] += int(reported.get("output_tokens", 0)) + int(
            reported.get("reasoning_output_tokens", 0)
        )
    return usage


def _event_error(events: str) -> str:
    """codex reports API and schema failures as stdout events, not on stderr."""
    for event in _events(events):
        if event.get("type") == "error" and event.get("message"):
            return " ".join(str(event["message"]).split())
        failure = (event.get("error") or {}).get("message")
        if failure:
            return " ".join(str(failure).split())
    return ""


def _final_message(message_path: Path, events: str) -> str:
    if message_path.exists():
        text = message_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    for event in reversed(list(_events(events))):
        item = event.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            return str(item["text"]).strip()
    return ""


def decode_json_text_values(payload: Any) -> Any:
    """Undo the JSON-text carriage that strict mode forces on Any-typed fields."""
    if isinstance(payload, dict):
        decoded = {}
        for key, value in payload.items():
            if key == "value" and isinstance(value, str):
                try:
                    decoded[key] = json.loads(value)
                    continue
                except json.JSONDecodeError:
                    pass
            decoded[key] = decode_json_text_values(value)
        return decoded
    if isinstance(payload, list):
        return [decode_json_text_values(item) for item in payload]
    return payload


async def run_codex(
    raw_text: str,
    settings: StructureSettings,
    scope: str,
    output_type: type[BaseModel],
    validate: Any = None,
) -> ModelRun:
    if shutil.which(CODEX_BINARY) is None:
        raise PipelineError(
            "missing_codex_binary", f"{CODEX_BINARY} is not on PATH", retryable=False
        )
    patch_hint = (
        "Send any JSON-Patch value as a JSON-encoded string.\n\n"
        if output_type is RepairPatch
        else ""
    )
    prompt = (
        f"{_instructions(output_type)}\n\nREQUEST SCOPE\n{scope}\n\n"
        f"Return only the JSON described by the output schema.\n"
        f"{patch_hint}\n{raw_text}"
    )
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="openacts-codex-") as work:
        schema_path = Path(work) / "schema.json"
        message_path = Path(work) / "last-message.json"
        schema_path.write_text(
            json.dumps(strict_schema(output_type.model_json_schema())),
            encoding="utf-8",
        )
        # subprocess.run in a worker thread owns the whole child lifecycle,
        # including killing it on timeout. Driving an asyncio subprocess here
        # instead leaks its transport when a timeout cancels communicate(), and
        # the loop then hangs forever in _cancel_all_tasks at shutdown.
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                _command(settings, schema_path, message_path),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=settings.request_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(
                "model_transient",
                f"codex exec exceeded {settings.request_timeout_seconds}s",
                retryable=True,
            ) from exc

        events = completed.stdout or ""
        if completed.returncode != 0:
            detail = _event_error(events) or (completed.stderr or "").strip()
            raise PipelineError(
                "model_transient",
                f"codex exec failed with status {completed.returncode}: "
                f"{detail[:2000]}",
                retryable=True,
            )
        message = _final_message(message_path, events)
        usage = _usage(events)

    if not message:
        raise PipelineError(
            "model_invalid_output", "codex exec returned no final message", retryable=True
        )
    try:
        output = output_type.model_validate(
            decode_json_text_values(json.loads(message))
        )
    except (ValidationError, json.JSONDecodeError) as exc:
        raise PipelineError(
            "model_invalid_output",
            f"{settings.primary_model} did not return valid structured output: "
            f"{str(exc)[:1200]}; reply began: {message[:400]}",
            retryable=True,
        ) from exc

    if validate is not None:
        try:
            validate(output)
        except PipelineError as validation_exc:
            raise RejectedModelOutput(
                settings.primary_model, output, validation_exc
            ) from validation_exc

    return ModelRun(
        output=output,
        model=settings.primary_model,
        output_mode="codex_output_schema",
        latency_seconds=round(time.perf_counter() - started, 3),
        usage=usage,
    )
