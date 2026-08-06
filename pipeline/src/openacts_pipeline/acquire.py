"""Acquire and fingerprint one approved PDF source."""

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from jsonschema import Draft202012Validator, FormatChecker
from pypdf import PdfReader
from referencing import Registry, Resource

from openacts_pipeline.classify import classify
from openacts_pipeline.common import (
    PipelineError,
    iso_timestamp,
    utc_now,
    write_json_result,
)
from openacts_pipeline.extract import extract

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schemas"
DEFAULT_CACHE_ROOT = REPO_ROOT / "source-cache"

MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 2_000
MAX_REDIRECTS = 5
MAX_ATTEMPTS = 2
REQUEST_TIMEOUT_SECONDS = 30
CHUNK_SIZE = 64 * 1024
REQUEST_FIELDS = {
    "url",
    "provider_name",
    "document_title",
    "document_publisher",
    "language",
    "source_class",
    "publication",
    "redistribution",
    "location_notes",
    "document_notes",
}
REQUIRED_REQUEST_FIELDS = {
    "url",
    "provider_name",
    "document_title",
    "document_publisher",
    "source_class",
}
TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}

AcquisitionError = PipelineError


@dataclass(frozen=True)
class Download:
    temporary_path: Path
    digest: str
    byte_length: int
    final_url: str
    status: int
    content_type: str | None
    content_length: str | None
    last_modified: str | None
    etag: str | None
    redirects: list[dict[str, Any]]
    warnings: list[str]
    attempts: int = 1


class _RedirectRecorder(HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.history: list[dict[str, Any]] = []

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        if len(self.history) >= MAX_REDIRECTS:
            raise AcquisitionError("too_many_redirects", "redirect limit exceeded")
        _validate_url(newurl)
        self.history.append({"status": code, "from": req.full_url, "to": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AcquisitionError("unsupported_url", "source URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise AcquisitionError(
            "unsupported_url", "source URL must not contain credentials"
        )
    if parsed.fragment:
        raise AcquisitionError(
            "unsupported_url", "source URL must not contain a fragment"
        )


def _required_string(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionError("invalid_request", f"{field} must be a string")
    return value.strip()


def load_request(path: Path) -> dict[str, Any]:
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcquisitionError(
            "invalid_request", f"cannot read request: {exc}"
        ) from exc

    if not isinstance(request, dict):
        raise AcquisitionError("invalid_request", "request must be a JSON object")

    missing = sorted(REQUIRED_REQUEST_FIELDS - request.keys())
    if missing:
        raise AcquisitionError(
            "invalid_request", f"request is missing: {', '.join(missing)}"
        )
    unknown = sorted(request.keys() - REQUEST_FIELDS)
    if unknown:
        raise AcquisitionError(
            "invalid_request", f"request has unknown fields: {', '.join(unknown)}"
        )

    normalized = {
        "url": _required_string(request, "url"),
        "provider_name": _required_string(request, "provider_name"),
        "document_title": _required_string(request, "document_title"),
        "document_publisher": _required_string(request, "document_publisher"),
        "language": request.get("language", "eng"),
        "source_class": _required_string(request, "source_class"),
        "publication": request.get("publication"),
        "redistribution": request.get(
            "redistribution",
            {"status": "not_researched", "license": None, "notes": None},
        ),
        "location_notes": request.get("location_notes"),
        "document_notes": request.get("document_notes", []),
    }
    _validate_url(normalized["url"])

    # Validate human-authored fields before network access. Measured fields use
    # harmless valid values here and are replaced after acquisition.
    candidate = _build_source_candidate(
        normalized,
        digest="0" * 64,
        byte_length=1,
        page_count=1,
        retrieved_at="2000-01-01T00:00:00Z",
        http_last_modified=None,
    )
    _validate_source(candidate, error_code="invalid_request")
    return normalized


def _build_source_candidate(
    request: dict[str, Any],
    *,
    digest: str,
    byte_length: int,
    page_count: int,
    retrieved_at: str,
    http_last_modified: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "record_type": "source",
        "source_id": f"sha256:{digest}",
        "document_title": request["document_title"],
        "document_publisher": request["document_publisher"],
        "language": request["language"],
        "source_class": request["source_class"],
        "publication": request["publication"],
        "media_type": "application/pdf",
        "byte_length": byte_length,
        "page_count": page_count,
        "text_layer": "unknown",
        "locations": [
            {
                "url": request["url"],
                "provider_name": request["provider_name"],
                "retrieved_at": retrieved_at,
                "http_last_modified": http_last_modified,
                "notes": request["location_notes"],
            }
        ],
        "redistribution": request["redistribution"],
        "document_notes": request["document_notes"],
    }


def _validate_source(source: dict[str, Any], *, error_code: str) -> None:
    schema_path = SCHEMA_DIR / "source.schema.json"
    registry = Registry().with_resources(
        (
            path.resolve().as_uri(),
            Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))),
        )
        for path in SCHEMA_DIR.glob("*.schema.json")
    )
    validator = Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": schema_path.resolve().as_uri(),
        },
        registry=registry,
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(source), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.path) or "source"
        raise AcquisitionError(error_code, f"{field}: {error.message}")


def _http_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return iso_timestamp(parsed)


def _download_once(url: str, cache_root: Path) -> Download:
    redirect_handler = _RedirectRecorder()
    opener = build_opener(redirect_handler)
    request = Request(url, headers={"User-Agent": "OpenActs/0.1 source acquisition"})

    try:
        response = opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
    except HTTPError as exc:
        retryable = exc.code in TRANSIENT_HTTP_STATUSES
        raise AcquisitionError(
            "http_transient" if retryable else "http_permanent",
            f"source returned HTTP {exc.code}",
            retryable=retryable,
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise AcquisitionError(
            "network_failure", f"source request failed: {exc}", retryable=True
        ) from exc

    with response:
        status = response.getcode()
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type")
        content_length = response.headers.get("Content-Length")
        last_modified = response.headers.get("Last-Modified")
        etag = response.headers.get("ETag")
        if content_type and content_type.split(";", 1)[0].strip().lower() in {
            "text/html",
            "application/xhtml+xml",
        }:
            raise AcquisitionError("not_pdf", "source returned HTML")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BYTES:
                    raise AcquisitionError(
                        "source_too_large", "source exceeds size limit"
                    )
            except ValueError:
                pass

        temporary_dir = cache_root / "tmp"
        temporary_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="acquire-", suffix=".tmp", dir=temporary_dir
        )
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        byte_length = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while True:
                    try:
                        chunk = response.read(CHUNK_SIZE)
                    except (HTTPException, OSError) as exc:
                        raise AcquisitionError(
                            "network_failure",
                            f"source stream failed: {exc}",
                            retryable=True,
                        ) from exc
                    if not chunk:
                        break
                    byte_length += len(chunk)
                    if byte_length > MAX_BYTES:
                        raise AcquisitionError(
                            "source_too_large", "source exceeds size limit"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            with temporary_path.open("rb") as handle:
                prefix = handle.read(1024)
            if b"%PDF-" not in prefix:
                raise AcquisitionError("not_pdf", "source bytes are not a PDF")
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    warnings: list[str] = []
    normalized_content_type = (
        content_type.split(";", 1)[0].strip().lower() if content_type else None
    )
    if normalized_content_type != "application/pdf":
        warnings.append(
            f"HTTP Content-Type was {content_type or 'absent'}; PDF bytes were verified"
        )
    return Download(
        temporary_path=temporary_path,
        digest=digest.hexdigest(),
        byte_length=byte_length,
        final_url=final_url,
        status=status,
        content_type=content_type,
        content_length=content_length,
        last_modified=last_modified,
        etag=etag,
        redirects=redirect_handler.history,
        warnings=warnings,
    )


def _download(
    url: str,
    cache_root: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Download:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return replace(_download_once(url, cache_root), attempts=attempt)
        except AcquisitionError as exc:
            if not exc.retryable or attempt == MAX_ATTEMPTS:
                raise
            sleep(random.uniform(0, min(2**attempt, 5)))
    raise RuntimeError("MAX_ATTEMPTS must be positive")


def _inspect_pdf(path: Path) -> int:
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise AcquisitionError("pdf_encrypted", "encrypted PDFs are unsupported")
        page_count = len(reader.pages)
    except AcquisitionError:
        raise
    except Exception as exc:
        raise AcquisitionError("pdf_unreadable", f"cannot read PDF: {exc}") from exc
    if page_count < 1:
        raise AcquisitionError("pdf_unreadable", "PDF has no pages")
    if page_count > MAX_PAGES:
        raise AcquisitionError("page_limit_exceeded", "PDF exceeds page limit")
    return page_count


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _store(download: Download, cache_root: Path) -> tuple[Path, bool]:
    destination = cache_root / "sha256" / download.digest[:2] / f"{download.digest}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        os.link(download.temporary_path, destination)
        created = True
    except FileExistsError:
        if _digest_file(destination) != download.digest:
            raise AcquisitionError(
                "cache_collision", "cached file does not match its digest path"
            )
    finally:
        download.temporary_path.unlink(missing_ok=True)
    return destination, created


def _write_result(
    cache_root: Path,
    result: dict[str, Any],
    *,
    started_at: datetime,
) -> Path:
    run_id = f"{started_at:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    return write_json_result(cache_root, result, Path("runs") / f"{run_id}.json")


def acquire(
    request_path: Path,
    *,
    execute: bool = False,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    request = load_request(request_path)
    if not execute:
        return {
            "status": "dry_run",
            "network_access": False,
            "url": request["url"],
            "cache_root": cache_root.as_posix(),
            "limits": {
                "max_bytes": MAX_BYTES,
                "max_pages": MAX_PAGES,
                "max_redirects": MAX_REDIRECTS,
                "max_attempts": MAX_ATTEMPTS,
                "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            },
        }

    started_at = utc_now()
    retrieved_at = iso_timestamp(started_at)
    download: Download | None = None
    try:
        download = _download(request["url"], cache_root, sleep=sleep)
        page_count = _inspect_pdf(download.temporary_path)
        candidate = _build_source_candidate(
            request,
            digest=download.digest,
            byte_length=download.byte_length,
            page_count=page_count,
            retrieved_at=retrieved_at,
            http_last_modified=_http_date(download.last_modified),
        )
        _validate_source(candidate, error_code="invalid_source_candidate")
        cache_path, cache_created = _store(download, cache_root)
        result = {
            "status": "success",
            "started_at": retrieved_at,
            "finished_at": iso_timestamp(utc_now()),
            "cache_path": cache_path.relative_to(cache_root).as_posix(),
            "cache_created": cache_created,
            "http": {
                "requested_url": request["url"],
                "final_url": download.final_url,
                "status": download.status,
                "content_type": download.content_type,
                "content_length": download.content_length,
                "last_modified": download.last_modified,
                "etag": download.etag,
                "redirects": download.redirects,
                "attempts": download.attempts,
            },
            "warnings": download.warnings,
            "source": candidate,
        }
        _write_result(cache_root, result, started_at=started_at)
        return result
    except AcquisitionError as exc:
        if download is not None:
            download.temporary_path.unlink(missing_ok=True)
        result = {
            "status": "failure",
            "started_at": retrieved_at,
            "finished_at": iso_timestamp(utc_now()),
            "url": request["url"],
            "error": exc.as_dict(),
        }
        _write_result(cache_root, result, started_at=started_at)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    acquire_parser = subparsers.add_parser(
        "acquire", help="download and fingerprint one approved PDF"
    )
    acquire_parser.add_argument("request", type=Path)
    acquire_parser.add_argument(
        "--execute",
        action="store_true",
        help="perform network access and write the local cache",
    )
    classify_parser = subparsers.add_parser(
        "classify", help="measure cached PDF text coverage"
    )
    classify_parser.add_argument("receipt", type=Path)
    extract_parser = subparsers.add_parser(
        "extract", help="extract native text from a classified PDF"
    )
    extract_parser.add_argument("classification", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "acquire":
            result = acquire(args.request, execute=args.execute)
        elif args.command == "classify":
            result = classify(args.receipt, cache_root=DEFAULT_CACHE_ROOT)
        else:
            result = extract(args.classification, cache_root=DEFAULT_CACHE_ROOT)
    except PipelineError as exc:
        print(
            json.dumps({"status": "failure", "error": exc.as_dict()}), file=sys.stderr
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
