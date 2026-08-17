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
DEFAULT_STRUCTURE_MAX_TOTAL_TOKENS = 2_000_000


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
    max_total_tokens: int = DEFAULT_STRUCTURE_MAX_TOTAL_TOKENS

    @classmethod
    def from_env(
        cls, environment: Mapping[str, str] | None = None
    ) -> "StructureSettings":
        values = os.environ if environment is None else environment
        api_key = values.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
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
            "OPENACTS_STRUCTURE_TIMEOUT_SECONDS", DEFAULT_STRUCTURE_TIMEOUT_SECONDS
        )
        concurrency = positive_integer(
            "OPENACTS_STRUCTURE_CONCURRENCY", DEFAULT_STRUCTURE_CONCURRENCY
        )
        repair_rounds = positive_integer(
            "OPENACTS_STRUCTURE_MAX_REPAIR_ROUNDS",
            DEFAULT_STRUCTURE_MAX_REPAIR_ROUNDS,
        )
        max_total_tokens = positive_integer(
            "OPENACTS_STRUCTURE_MAX_TOTAL_TOKENS",
            DEFAULT_STRUCTURE_MAX_TOTAL_TOKENS,
        )

        return cls(
            api_key=api_key,
            base_url=values.get(
                "OPENACTS_DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL
            ).rstrip("/"),
            primary_model=values.get("OPENACTS_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL),
            request_timeout_seconds=timeout,
            concurrency=concurrency,
            max_repair_rounds=repair_rounds,
            max_total_tokens=max_total_tokens,
        )
