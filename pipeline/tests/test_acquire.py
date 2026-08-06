from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Self

import pytest
from pypdf import PdfWriter

import openacts_pipeline.acquire as acquire_module
from openacts_pipeline.acquire import AcquisitionError, acquire


def pdf_bytes(width: int = 72) -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=72)
    writer.write(output)
    return output.getvalue()


@contextmanager
def source_server(payload: bytes) -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {"payload": payload, "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["calls"].append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/document.pdf")
                self.end_headers()
                return
            if self.path == "/missing":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/transient" and state["calls"].count(self.path) == 1:
                self.send_response(503)
                self.end_headers()
                return
            if self.path == "/html":
                body = b"<!doctype html><title>Not a PDF</title>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = (
                b"%PDF-not-a-real-pdf"
                if self.path == "/malformed"
                else state["payload"]
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Last-Modified", "Wed, 06 Aug 2026 12:00:00 GMT")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def write_request(path: Path, url: str) -> Path:
    request = {
        "url": url,
        "provider_name": "Test provider",
        "document_title": "Test Act",
        "document_publisher": "Test publisher",
        "source_class": "official_agency_copy",
    }
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def test_dry_run_has_no_network_or_writes(tmp_path: Path) -> None:
    with source_server(pdf_bytes()) as (base_url, state):
        request = write_request(tmp_path / "request.json", f"{base_url}/document.pdf")
        cache_root = tmp_path / "source-cache"

        result = acquire(request, cache_root=cache_root)

    assert result["status"] == "dry_run"
    assert result["network_access"] is False
    assert result["cache_root"] == cache_root.as_posix()
    assert state["calls"] == []
    assert not cache_root.exists()


def test_unknown_source_class_is_rejected_before_network(tmp_path: Path) -> None:
    with source_server(pdf_bytes()) as (base_url, state):
        request = write_request(tmp_path / "request.json", f"{base_url}/document.pdf")
        request_data = json.loads(request.read_text(encoding="utf-8"))
        request_data["source_class"] = "unknown"
        request.write_text(json.dumps(request_data), encoding="utf-8")

        with pytest.raises(AcquisitionError) as caught:
            acquire(request, execute=True, cache_root=tmp_path / "source-cache")

    assert caught.value.code == "invalid_request"
    assert state["calls"] == []


def test_execute_caches_and_reuses_valid_pdf(tmp_path: Path) -> None:
    payload = pdf_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    with source_server(payload) as (base_url, state):
        request = write_request(tmp_path / "request.json", f"{base_url}/redirect")
        cache_root = tmp_path / "source-cache"

        first = acquire(request, execute=True, cache_root=cache_root)
        second = acquire(request, execute=True, cache_root=cache_root)

    cached = cache_root / "sha256" / digest[:2] / f"{digest}.pdf"
    assert cached.read_bytes() == payload
    assert first["cache_created"] is True
    assert second["cache_created"] is False
    assert first["source"]["source_id"] == f"sha256:{digest}"
    assert first["source"]["page_count"] == 1
    assert first["source"]["text_layer"] == "unknown"
    assert first["http"]["redirects"][0]["to"].endswith("/document.pdf")
    assert state["calls"] == [
        "/redirect",
        "/document.pdf",
        "/redirect",
        "/document.pdf",
    ]
    assert len(list((cache_root / "runs").glob("*.json"))) == 2


def test_changed_bytes_get_a_new_identity(tmp_path: Path) -> None:
    with source_server(pdf_bytes()) as (base_url, state):
        request = write_request(tmp_path / "request.json", f"{base_url}/document.pdf")
        cache_root = tmp_path / "source-cache"
        first = acquire(request, execute=True, cache_root=cache_root)
        state["payload"] = pdf_bytes(width=144)
        second = acquire(request, execute=True, cache_root=cache_root)

    assert first["source"]["source_id"] != second["source"]["source_id"]
    assert len(list((cache_root / "sha256").glob("*/*.pdf"))) == 2


def test_transient_failure_retries_once(tmp_path: Path) -> None:
    with source_server(pdf_bytes()) as (base_url, state):
        request = write_request(tmp_path / "request.json", f"{base_url}/transient")

        result = acquire(
            request,
            execute=True,
            cache_root=tmp_path / "source-cache",
            sleep=lambda _: None,
        )

    assert result["http"]["attempts"] == 2
    assert state["calls"] == ["/transient", "/transient"]


def test_stream_timeout_retries_and_writes_failure_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"opens": 0}

    class Response:
        def __init__(self) -> None:
            self.headers = {"Content-Type": "application/pdf"}

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def getcode(self) -> int:
            return 200

        def geturl(self) -> str:
            return "https://example.test/document.pdf"

        def read(self, size: int) -> bytes:
            raise TimeoutError("stream stalled")

    class Opener:
        def open(self, request: object, timeout: int) -> Response:
            state["opens"] += 1
            return Response()

    monkeypatch.setattr(acquire_module, "build_opener", lambda _: Opener())
    request = write_request(
        tmp_path / "request.json", "https://example.test/document.pdf"
    )
    cache_root = tmp_path / "source-cache"

    with pytest.raises(AcquisitionError) as caught:
        acquire(request, execute=True, cache_root=cache_root, sleep=lambda _: None)

    assert caught.value.code == "network_failure"
    assert caught.value.retryable is True
    assert state["opens"] == 2
    assert len(list((cache_root / "runs").glob("*.json"))) == 1
    assert not list((cache_root / "tmp").glob("*"))


@pytest.mark.parametrize(
    ("path", "error_code"),
    [
        ("/html", "not_pdf"),
        ("/malformed", "pdf_unreadable"),
        ("/missing", "http_permanent"),
    ],
)
def test_invalid_sources_never_enter_cache(
    tmp_path: Path, path: str, error_code: str
) -> None:
    with source_server(pdf_bytes()) as (base_url, state):
        request = write_request(tmp_path / "request.json", f"{base_url}{path}")
        cache_root = tmp_path / "source-cache"

        with pytest.raises(AcquisitionError) as caught:
            acquire(request, execute=True, cache_root=cache_root, sleep=lambda _: None)

    assert caught.value.code == error_code
    assert not list((cache_root / "sha256").glob("*/*.pdf"))
    assert not list((cache_root / "tmp").glob("*"))
    assert len(list((cache_root / "runs").glob("*.json"))) == 1
    assert state["calls"] == [path]
