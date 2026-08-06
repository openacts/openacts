"""Shared primitives for local pipeline stages."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class PipelineError(Exception):
    """A structured pipeline failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def write_json_result(
    cache_root: Path, result: dict[str, Any], relative_path: Path
) -> Path:
    result["result_path"] = relative_path.as_posix()
    destination = cache_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination
