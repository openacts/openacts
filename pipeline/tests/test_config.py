import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_PRIMARY_MODEL,
    StructureSettings,
)


def test_structure_settings_are_typed_and_fail_loudly() -> None:
    settings = StructureSettings.from_env({"DEEPSEEK_API_KEY": "secret"})

    assert settings.api_key == "secret"
    assert settings.base_url == DEFAULT_DEEPSEEK_BASE_URL
    assert settings.primary_model == DEFAULT_PRIMARY_MODEL
    assert settings.request_timeout_seconds == 300
    assert settings.concurrency == 4
    assert settings.max_repair_rounds == 2
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
