"""Typed configuration for model-backed pipeline stages."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from openacts_pipeline.common import PipelineError

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_PRIMARY_MODEL = "deepseek-v4-pro"
DEFAULT_STRUCTURE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class StructureSettings:
    api_key: str
    base_url: str
    primary_model: str
    request_timeout_seconds: int

    @classmethod
    def from_env(
        cls, environment: Mapping[str, str] | None = None
    ) -> "StructureSettings":
        values = os.environ if environment is None else environment
        api_key = values.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise PipelineError("missing_model_api_key", "DEEPSEEK_API_KEY is required")

        try:
            timeout = int(
                values.get(
                    "OPENACTS_STRUCTURE_TIMEOUT_SECONDS",
                    str(DEFAULT_STRUCTURE_TIMEOUT_SECONDS),
                )
            )
        except ValueError as exc:
            raise PipelineError(
                "invalid_configuration",
                "OPENACTS_STRUCTURE_TIMEOUT_SECONDS must be an integer",
            ) from exc
        if timeout < 1:
            raise PipelineError(
                "invalid_configuration",
                "OPENACTS_STRUCTURE_TIMEOUT_SECONDS must be positive",
            )

        return cls(
            api_key=api_key,
            base_url=values.get(
                "OPENACTS_DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL
            ).rstrip("/"),
            primary_model=values.get("OPENACTS_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL),
            request_timeout_seconds=timeout,
        )
