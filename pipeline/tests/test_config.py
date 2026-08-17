import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_PRIMARY_MODEL,
    ProjectionSettings,
    StructureSettings,
)


def test_structure_settings_are_typed_and_fail_loudly() -> None:
    settings = StructureSettings.from_env({"DEEPSEEK_API_KEY": "secret"})

    assert settings.api_key == "secret"
    assert settings.base_url == DEFAULT_DEEPSEEK_BASE_URL
    assert settings.primary_model == DEFAULT_PRIMARY_MODEL
    assert settings.request_timeout_seconds == 300
    assert settings.concurrency == 4
    assert settings.max_repair_rounds == 3
    assert settings.max_total_tokens == 2_000_000

    with pytest.raises(PipelineError) as missing:
        StructureSettings.from_env({})
    assert missing.value.code == "missing_model_api_key"

    with pytest.raises(PipelineError) as invalid:
        StructureSettings.from_env(
            {
                "DEEPSEEK_API_KEY": "secret",
                "OPENACTS_STRUCTURE_TIMEOUT_SECONDS": "never",
            }
        )
    assert invalid.value.code == "invalid_configuration"

    with pytest.raises(PipelineError) as invalid_concurrency:
        StructureSettings.from_env(
            {
                "DEEPSEEK_API_KEY": "secret",
                "OPENACTS_STRUCTURE_CONCURRENCY": "0",
            }
        )
    assert invalid_concurrency.value.code == "invalid_configuration"


def test_projection_settings_require_and_hide_the_database_url() -> None:
    database_url = "postgresql://openacts:secret@localhost/openacts"
    settings = ProjectionSettings.from_env(
        {"OPENACTS_PROJECTION_DATABASE_URL": f"  {database_url}  "}
    )

    assert settings.database_url == database_url
    assert database_url not in repr(settings)
    assert "secret" not in repr(settings)

    with pytest.raises(PipelineError) as missing:
        ProjectionSettings.from_env({})
    assert missing.value.code == "missing_projection_database_url"
