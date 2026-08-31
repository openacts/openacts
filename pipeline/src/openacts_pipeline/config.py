"""Typed configuration for pipeline stages."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from openacts_pipeline.common import PipelineError

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_PRIMARY_MODEL = "deepseek-v4-pro"
DEFAULT_STRUCTURE_TIMEOUT_SECONDS = 300
DEFAULT_STRUCTURE_CONCURRENCY = 4
DEFAULT_STRUCTURE_MAX_REPAIR_ROUNDS = 3
DEFAULT_STRUCTURE_MAX_UNIT_SPLITS = 2
DEFAULT_STRUCTURE_MAX_TOTAL_TOKENS = 2_000_000
# The largest unit codex-default has structured successfully is 73k characters;
# 90k and above have returned incomplete drafts or exhausted the request timeout.
DEFAULT_STRUCTURE_MAX_UNIT_CHARACTERS = 75_000
DEFAULT_MODEL_BACKEND = "codex"
DEFAULT_CODEX_REASONING_EFFORT = "low"
DEFAULT_CODEX_MODEL_LABEL = "codex-default"
CODEX_BASE_URL = "codex://local"
MODEL_BACKENDS = frozenset({"deepseek", "codex"})

# codex spends ~90s on a trivial prompt, so the shared default would turn ordinary
# long passes into spurious model_transient failures. A backend that needs longer
# than the default says so here, and an explicit timeout still wins.
BACKEND_TIMEOUT_SECONDS = {"codex": 900}


@dataclass(frozen=True)
class ProjectionSettings:
    database_url: str = field(repr=False)

    @classmethod
    def from_env(
        cls, environment: Mapping[str, str] | None = None
    ) -> "ProjectionSettings":
        values = os.environ if environment is None else environment
        database_url = values.get("OPENACTS_PROJECTION_DATABASE_URL", "").strip()
        if not database_url:
            raise PipelineError(
                "missing_projection_database_url",
                "OPENACTS_PROJECTION_DATABASE_URL is required",
            )
        return cls(database_url=database_url)


@dataclass(frozen=True)
class StructureSettings:
    api_key: str
    base_url: str
    primary_model: str
    request_timeout_seconds: int
    concurrency: int = DEFAULT_STRUCTURE_CONCURRENCY
    max_repair_rounds: int = DEFAULT_STRUCTURE_MAX_REPAIR_ROUNDS
    max_unit_splits: int = DEFAULT_STRUCTURE_MAX_UNIT_SPLITS
    max_total_tokens: int = DEFAULT_STRUCTURE_MAX_TOTAL_TOKENS
    max_unit_characters: int = DEFAULT_STRUCTURE_MAX_UNIT_CHARACTERS
    backend: str = DEFAULT_MODEL_BACKEND
    codex_reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT

    @classmethod
    def from_env(
        cls, environment: Mapping[str, str] | None = None
    ) -> "StructureSettings":
        values = os.environ if environment is None else environment
        backend = values.get("OPENACTS_MODEL_BACKEND", DEFAULT_MODEL_BACKEND).strip()
        if backend not in MODEL_BACKENDS:
            raise PipelineError(
                "invalid_configuration",
                f"OPENACTS_MODEL_BACKEND must be one of {sorted(MODEL_BACKENDS)}",
            )
        api_key = values.get("DEEPSEEK_API_KEY", "").strip()
        if backend == "deepseek" and not api_key:
            raise PipelineError("missing_model_api_key", "DEEPSEEK_API_KEY is required")

        def positive_integer(name: str, default: int) -> int:
            try:
                value = int(values.get(name, str(default)))
            except ValueError as exc:
                raise PipelineError(
                    "invalid_configuration", f"{name} must be an integer"
                ) from exc
            if value < 1:
                raise PipelineError("invalid_configuration", f"{name} must be positive")
            return value

        timeout = positive_integer(
            "OPENACTS_STRUCTURE_TIMEOUT_SECONDS",
            BACKEND_TIMEOUT_SECONDS.get(backend, DEFAULT_STRUCTURE_TIMEOUT_SECONDS),
        )
        concurrency = positive_integer(
            "OPENACTS_STRUCTURE_CONCURRENCY", DEFAULT_STRUCTURE_CONCURRENCY
        )
        repair_rounds = positive_integer(
            "OPENACTS_STRUCTURE_MAX_REPAIR_ROUNDS",
            DEFAULT_STRUCTURE_MAX_REPAIR_ROUNDS,
        )
        unit_splits = positive_integer(
            "OPENACTS_STRUCTURE_MAX_UNIT_SPLITS",
            DEFAULT_STRUCTURE_MAX_UNIT_SPLITS,
        )
        max_total_tokens = positive_integer(
            "OPENACTS_STRUCTURE_MAX_TOTAL_TOKENS",
            DEFAULT_STRUCTURE_MAX_TOTAL_TOKENS,
        )
        max_unit_characters = positive_integer(
            "OPENACTS_STRUCTURE_MAX_UNIT_CHARACTERS",
            DEFAULT_STRUCTURE_MAX_UNIT_CHARACTERS,
        )

        # The work key is derived from base_url and primary_model, so the codex
        # backend must report its own pair or its checkpoints collide with DeepSeek's.
        if backend == "codex":
            base_url = CODEX_BASE_URL
            primary_model = (
                values.get("OPENACTS_CODEX_MODEL", "").strip()
                or DEFAULT_CODEX_MODEL_LABEL
            )
        else:
            base_url = values.get(
                "OPENACTS_DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL
            ).rstrip("/")
            primary_model = values.get("OPENACTS_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL)

        return cls(
            api_key=api_key,
            base_url=base_url,
            primary_model=primary_model,
            request_timeout_seconds=timeout,
            concurrency=concurrency,
            max_repair_rounds=repair_rounds,
            max_unit_splits=unit_splits,
            max_total_tokens=max_total_tokens,
            max_unit_characters=max_unit_characters,
            backend=backend,
            codex_reasoning_effort=values.get(
                "OPENACTS_CODEX_REASONING_EFFORT", DEFAULT_CODEX_REASONING_EFFORT
            ).strip(),
        )
