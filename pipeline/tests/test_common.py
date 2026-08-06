import json
from datetime import UTC, datetime
from pathlib import Path

from openacts_pipeline.common import (
    PipelineError,
    iso_timestamp,
    utc_now,
    write_json_result,
)


def test_shared_pipeline_primitives(tmp_path: Path) -> None:
    error = PipelineError("broken", "something broke")
    assert error.as_dict() == {
        "code": "broken",
        "message": "something broke",
        "retryable": False,
    }
    assert iso_timestamp(datetime(2026, 8, 6, 12, tzinfo=UTC)) == "2026-08-06T12:00:00Z"
    assert utc_now().tzinfo is UTC

    result = {"status": "success"}
    destination = write_json_result(tmp_path, result, Path("runs/test.json"))

    assert result["result_path"] == "runs/test.json"
    assert json.loads(destination.read_text(encoding="utf-8")) == result
    assert not list(destination.parent.glob("*.tmp"))
