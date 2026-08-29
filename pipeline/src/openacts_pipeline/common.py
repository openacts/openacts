"""Shared primitives for local pipeline stages."""

import hashlib
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


def local_clock(stamp: str) -> str:
    """A recorded UTC timestamp as a wall clock reading, for display only.

    Artifacts stay in UTC so the cache sorts and travels. An operator reading
    progress is comparing it against the clock on their wall, and a bare time
    with its `Z` dropped silently reads as local when it is not.
    """
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return ""


def verify_cached_pdf(
    cache_root: Path,
    relative_path: Path,
    *,
    expected_byte_length: int,
    expected_digest: str,
) -> Path:
    cached_pdf = cache_root / relative_path
    try:
        byte_length = cached_pdf.stat().st_size
    except OSError as exc:
        raise PipelineError("cache_missing", f"cannot read cached PDF: {exc}") from exc
    if byte_length != expected_byte_length:
        raise PipelineError(
            "cache_size_mismatch", "cached PDF byte length changed after acquisition"
        )
    try:
        with cached_pdf.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise PipelineError(
            "cache_unreadable", f"cannot hash cached PDF: {exc}"
        ) from exc
    if digest != expected_digest:
        raise PipelineError(
            "cache_digest_mismatch", "cached PDF digest changed after acquisition"
        )
    return cached_pdf


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
