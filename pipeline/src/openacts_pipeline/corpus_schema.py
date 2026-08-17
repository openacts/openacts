"""Validate canonical records against an explicit schema directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import CannotDetermineSpecification, Unresolvable

from openacts_pipeline.common import PipelineError

DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"
SCHEMA_FILES = {
    "act": "act.schema.json",
    "provision": "provision.schema.json",
    "source": "source.schema.json",
    "citation": "citation.schema.json",
}
REQUIRED_SCHEMA_FILES = {
    *SCHEMA_FILES.values(),
    "common.schema.json",
    "table.schema.json",
}


def build_registry(schema_dir: Path) -> Registry:
    try:
        paths = sorted(schema_dir.glob("*.schema.json"))
    except OSError as exc:
        raise PipelineError(
            "invalid_corpus_schema", f"cannot inspect corpus schemas: {exc}"
        ) from exc
    names = {path.name for path in paths}
    if not REQUIRED_SCHEMA_FILES <= names:
        missing = sorted(REQUIRED_SCHEMA_FILES - names)
        raise PipelineError(
            "invalid_corpus_schema", "missing schemas: " + ", ".join(missing)
        )
    try:
        resources = []
        for path in paths:
            contents = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(contents, dict):
                raise TypeError(f"{path.name} must contain a JSON object")
            Draft202012Validator.check_schema(contents)
            resources.append(
                (path.resolve().as_uri(), Resource.from_contents(contents))
            )
        return Registry().with_resources(resources)
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        SchemaError,
        CannotDetermineSpecification,
    ) as exc:
        raise PipelineError(
            "invalid_corpus_schema", f"cannot load corpus schemas: {exc}"
        ) from exc


def validate_record(
    record_type: str,
    record: dict[str, Any],
    *,
    schema_dir: Path = DEFAULT_SCHEMA_DIR,
    registry: Registry | None = None,
) -> None:
    schema_path = schema_dir / SCHEMA_FILES[record_type]
    validator = Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": schema_path.resolve().as_uri(),
        },
        registry=registry or build_registry(schema_dir),
        format_checker=FormatChecker(),
    )
    try:
        errors = sorted(
            validator.iter_errors(record), key=lambda error: list(error.path)
        )
    except Unresolvable as exc:
        raise PipelineError(
            "invalid_corpus_schema", f"cannot resolve corpus schema: {exc}"
        ) from exc
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.path) or record_type
        raise PipelineError("invalid_corpus_record", f"{field}: {error.message}")
