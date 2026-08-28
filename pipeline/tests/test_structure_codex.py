import json

import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import StructureSettings
from openacts_pipeline.structure_codex import (
    _command,
    _event_error,
    _final_message,
    _usage,
    decode_json_text_values,
    strict_schema,
)
from openacts_pipeline.structure_schema import (
    RepairOperation,
    RepairPatch,
    RepairPlan,
    StructureDraft,
    StructurePlan,
)


def _codex_settings(**overrides: object) -> StructureSettings:
    values = {"OPENACTS_MODEL_BACKEND": "codex", **overrides}
    return StructureSettings.from_env(values)  # type: ignore[arg-type]


@pytest.mark.parametrize("model", [StructurePlan, RepairPlan, StructureDraft])
def test_strict_schema_requires_every_property_and_forbids_extras(model) -> None:
    def walk(node: object, path: str = "root") -> None:
        if isinstance(node, dict):
            if "properties" in node:
                assert set(node["properties"]) == set(node["required"]), path
                assert node["additionalProperties"] is False, path
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(strict_schema(model.model_json_schema()))


def test_strict_schema_drops_keywords_openai_rejects() -> None:
    strict = strict_schema(StructurePlan.model_json_schema())
    for node in (strict, *strict.get("$defs", {}).values()):
        for value in node.get("properties", {}).values():
            assert not {"minLength", "minItems", "default", "pattern"} & value.keys()


def test_strict_schema_restates_dropped_constraints_in_the_description() -> None:
    """Without minItems the model returns an empty list that then fails validation."""
    operations = strict_schema(RepairPatch.model_json_schema())["properties"][
        "operations"
    ]
    assert "minItems=1" in operations["description"]
    assert "maxItems=24" in operations["description"]


def test_strict_schema_rewrites_one_of_which_strict_mode_rejects() -> None:
    """Pydantic emits oneOf for the content-block union; strict mode forbids it."""
    assert '"oneOf"' in json.dumps(StructureDraft.model_json_schema())
    assert '"oneOf"' not in json.dumps(strict_schema(StructureDraft.model_json_schema()))


def test_codex_command_reads_the_prompt_from_stdin() -> None:
    command = _command(_codex_settings(), "schema.json", "out.json")
    assert command[-1] == "-"
    assert "--output-schema" in command
    assert "model_reasoning_effort=low" in command
    assert "--model" not in command


def test_codex_command_pins_an_explicit_model() -> None:
    command = _command(
        _codex_settings(OPENACTS_CODEX_MODEL="gpt-5.6-sol"), "s.json", "o.json"
    )
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"


def test_usage_sums_reasoning_into_output_tokens() -> None:
    events = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 20000,
                        "output_tokens": 30,
                        "reasoning_output_tokens": 12,
                    },
                }
            ),
        ]
    )
    assert _usage(events) == {
        "requests": 1,
        "input_tokens": 20000,
        "output_tokens": 42,
    }


def test_final_message_falls_back_to_the_agent_message_event(tmp_path) -> None:
    missing = tmp_path / "absent.json"
    events = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{\"a\":1}"}}
    )
    assert _final_message(missing, events) == '{"a":1}'


def test_codex_settings_do_not_require_a_deepseek_key() -> None:
    settings = _codex_settings()
    assert settings.backend == "codex"
    assert settings.base_url == "codex://local"

    with pytest.raises(PipelineError, match="DEEPSEEK_API_KEY"):
        StructureSettings.from_env({})


def test_event_error_reads_the_failure_codex_reports_on_stdout() -> None:
    events = "\n".join(
        [
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "error", "message": "invalid_json_schema: oneOf"}),
        ]
    )
    assert _event_error(events) == "invalid_json_schema: oneOf"


def test_event_error_reads_a_nested_turn_failure() -> None:
    events = json.dumps({"type": "turn.failed", "error": {"message": "rate  limited"}})
    assert _event_error(events) == "rate limited"


def test_strict_schema_types_any_fields_as_nullable_json_text() -> None:
    """Strict mode rejects untyped nodes and cannot express arbitrary JSON.

    It also demands every property, so an operation that carries no value must be
    able to send null; an empty string fails RepairOperation's own validator.
    """
    raw = RepairPatch.model_json_schema()
    assert "type" not in raw["$defs"]["RepairOperation"]["properties"]["value"]
    strict = strict_schema(raw)
    value = strict["$defs"]["RepairOperation"]["properties"]["value"]
    assert value["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_a_null_value_survives_decoding_for_move_and_remove() -> None:
    payload = {"operations": [{"op": "remove", "path": "/nodes/0", "value": None}]}
    assert decode_json_text_values(payload) == payload
    RepairOperation.model_validate(
        {"op": "remove", "path": "/nodes/0/children/1", "value": None}
    )


def test_decode_json_text_values_restores_patch_values() -> None:
    payload = {"operations": [{"op": "add", "value": '{"node_type": "section"}'}]}
    assert decode_json_text_values(payload) == {
        "operations": [{"op": "add", "value": {"node_type": "section"}}]
    }


def test_decode_json_text_values_leaves_plain_strings_alone() -> None:
    payload = {"operations": [{"op": "replace", "value": "a plain heading"}]}
    assert decode_json_text_values(payload) == payload


@pytest.mark.parametrize(
    "model", [RepairPatch, RepairPlan, StructurePlan, StructureDraft]
)
def test_strict_schema_never_types_a_schema_container(model) -> None:
    """$defs and properties map names to schemas; typing them corrupts the schema."""
    strict = strict_schema(model.model_json_schema())
    assert "type" not in strict.get("$defs", {})
    for definition in strict.get("$defs", {}).values():
        assert "type" not in definition.get("properties", {})
    assert "type" not in strict.get("properties", {})
