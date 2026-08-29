import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from openacts_pipeline.common import (
    PipelineError,
    decode_json_with_trailing_delimiters,
    iso_timestamp,
    utc_now,
    verify_cached_pdf,
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

    payload = b"cached PDF"
    digest = hashlib.sha256(payload).hexdigest()
    relative_path = Path("sha256") / digest[:2] / f"{digest}.pdf"
    cached_pdf = tmp_path / relative_path
    cached_pdf.parent.mkdir(parents=True)
    cached_pdf.write_bytes(payload)
    assert (
        verify_cached_pdf(
            tmp_path,
            relative_path,
            expected_byte_length=len(payload),
            expected_digest=digest,
        )
        == cached_pdf
    )


def test_stray_closing_delimiters_after_a_value_are_recovered() -> None:
    assert decode_json_with_trailing_delimiters('{"a": 1}') == {"a": 1}
    assert decode_json_with_trailing_delimiters('{"a": 1}}]}') == {"a": 1}
    assert decode_json_with_trailing_delimiters('  {"a": 1}  ]} ') == {"a": 1}


def test_real_content_after_a_value_is_refused_rather_than_dropped() -> None:
    """A second reply is a truncation or duplication, not a stray delimiter."""
    assert decode_json_with_trailing_delimiters('{"a": 1}{"b": 2}') is None
    assert decode_json_with_trailing_delimiters('{"a": 1} trailing prose') is None
    assert decode_json_with_trailing_delimiters('{"a": 1},{"b": 2}') is None
    assert decode_json_with_trailing_delimiters("not json at all") is None
    assert decode_json_with_trailing_delimiters("") is None
