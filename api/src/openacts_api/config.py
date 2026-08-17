"""Typed API configuration."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    application_revision: str
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if environment is None else environment
        database_url = values.get("OPENACTS_API_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("OPENACTS_API_DATABASE_URL is required")

        revision = values.get("OPENACTS_APPLICATION_REVISION", "").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(
                "OPENACTS_APPLICATION_REVISION must be a full lowercase Git commit"
            )

        origins = tuple(
            origin.strip()
            for origin in values.get("OPENACTS_CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        if "*" in origins:
            raise ValueError("OPENACTS_CORS_ORIGINS cannot contain '*'")

        return cls(
            database_url=database_url,
            application_revision=revision,
            cors_origins=origins,
        )
