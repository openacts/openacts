from hashlib import sha256
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openacts_api.app import create_app
from openacts_api.config import Settings
from openacts_api.database import (
    ActiveRelease,
    ProjectionUnavailable,
    _ordered_source_ids,
)

REVISION = "a" * 40
ACT_ID = "ng-federal-act-2023-37"
MISSING_ACT_ID = "ng-federal-act-2024-missing"
SOURCE_ID = f"sha256:{'c' * 64}"
MISSING_SOURCE_ID = f"sha256:{'d' * 64}"
RELEASE = ActiveRelease(
    release_tag="corpus-v0.0.0",
    commit_sha="b" * 40,
    canonical_schema_versions=("0.1.0",),
)
ACT_SUMMARY: dict[str, Any] = {
    "act_id": ACT_ID,
    "official_title": "Nigeria Data Protection Act, 2023",
    "short_title": "Nigeria Data Protection Act",
    "year": 2023,
    "number": "37",
    "citation": "Act No. 37 of 2023",
    "text_kind": "as_enacted",
    "status": "unknown",
    "checked_through_date": None,
}
ACT_RECORD: dict[str, Any] = {
    "schema_version": "0.1.0",
    "record_type": "act",
    "act_id": ACT_ID,
    "source_refs": [
        {"source_id": SOURCE_ID, "role": "authoritative_text"},
        {"source_id": SOURCE_ID, "role": "comparison_copy"},
    ],
}
SOURCE_RECORD: dict[str, Any] = {
    "schema_version": "0.1.0",
    "record_type": "source",
    "source_id": SOURCE_ID,
    "document_title": "Nigeria Data Protection Act, 2023",
}


class FakeDatabase:
    def __init__(self, release: ActiveRelease | None = RELEASE) -> None:
        self.release = release
        self.active_release_calls = 0
        self.reader_calls: list[tuple[object, ...]] = []

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def active_release(self) -> ActiveRelease:
        self.active_release_calls += 1
        if self.release is None:
            raise ProjectionUnavailable
        return self.release

    def list_acts(
        self, release_tag: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        self.reader_calls.append(("list_acts", release_tag, offset, limit))
        return ([ACT_SUMMARY] if offset == 0 else []), 1

    def get_act(
        self, release_tag: str, act_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        self.reader_calls.append(("get_act", release_tag, act_id))
        return (ACT_RECORD, [SOURCE_RECORD]) if act_id == ACT_ID else None

    def get_source(self, release_tag: str, source_id: str) -> dict[str, Any] | None:
        self.reader_calls.append(("get_source", release_tag, source_id))
        return SOURCE_RECORD if source_id == SOURCE_ID else None


def settings() -> Settings:
    return Settings(
        database_url="postgresql://unused",
        application_revision=REVISION,
        cors_origins=("http://localhost:5173",),
    )


def test_settings_validate_required_and_public_values() -> None:
    configured = Settings.from_env(
        {
            "OPENACTS_API_DATABASE_URL": "postgresql://reader@db/openacts",
            "OPENACTS_APPLICATION_REVISION": REVISION,
            "OPENACTS_CORS_ORIGINS": (
                "http://localhost:5173, https://openacts.example"
            ),
        }
    )
    assert configured.cors_origins == (
        "http://localhost:5173",
        "https://openacts.example",
    )
    assert "reader@db" not in repr(configured)

    with pytest.raises(ValueError):
        Settings.from_env({"OPENACTS_APPLICATION_REVISION": REVISION})
    with pytest.raises(ValueError):
        Settings.from_env(
            {
                "OPENACTS_API_DATABASE_URL": "postgresql://unused",
                "OPENACTS_APPLICATION_REVISION": "short",
            }
        )
    with pytest.raises(ValueError):
        Settings.from_env(
            {
                "OPENACTS_API_DATABASE_URL": "postgresql://unused",
                "OPENACTS_APPLICATION_REVISION": REVISION,
                "OPENACTS_CORS_ORIGINS": "*",
            }
        )


def test_health_is_live_without_reading_the_projection() -> None:
    database = FakeDatabase(release=None)
    with TestClient(create_app(settings(), database)) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application_revision": REVISION,
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["openacts-application-revision"] == REVISION
    assert database.active_release_calls == 0


def test_readiness_and_metadata_resolve_one_release_per_request() -> None:
    database = FakeDatabase()
    with TestClient(create_app(settings(), database)) as client:
        ready = client.get("/readyz")
        metadata = client.get("/v1/meta")
        not_modified = client.get(
            "/v1/meta", headers={"If-None-Match": metadata.headers["etag"]}
        )
        weak_not_modified = client.get(
            "/v1/meta", headers={"If-None-Match": f"W/{metadata.headers['etag']}"}
        )
        preflight = client.options(
            "/v1/meta",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "If-None-Match",
            },
        )

    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "application_revision": REVISION,
        "corpus_release": RELEASE.release_tag,
    }
    assert metadata.json() == {
        "meta": {
            "api_version": "v1",
            "application_revision": REVISION,
            "corpus_release": RELEASE.release_tag,
        },
        "data": {
            "corpus_commit": RELEASE.commit_sha,
            "canonical_schema_versions": ["0.1.0"],
        },
    }
    assert metadata.headers["cache-control"] == ("public, max-age=0, must-revalidate")
    assert metadata.headers["etag"] == f'"{sha256(metadata.content).hexdigest()}"'
    assert metadata.headers["openacts-corpus-release"] == RELEASE.release_tag
    assert not_modified.status_code == 304
    assert not not_modified.content
    assert not_modified.headers["etag"] == metadata.headers["etag"]
    assert not_modified.headers["openacts-application-revision"] == REVISION
    assert not_modified.headers["openacts-corpus-release"] == RELEASE.release_tag
    assert weak_not_modified.status_code == 304
    assert not weak_not_modified.content
    assert preflight.status_code == 200
    assert "if-none-match" in preflight.headers["access-control-allow-headers"].lower()
    assert database.active_release_calls == 4


def test_unavailable_projection_uses_the_typed_error_contract() -> None:
    database = FakeDatabase(release=None)
    with TestClient(create_app(settings(), database)) as client:
        response = client.get("/readyz")

    body = response.json()
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert body["meta"]["corpus_release"] is None
    assert body["error"]["code"] == "projection_unavailable"
    assert body["error"]["retryable"] is True
    assert body["error"]["request_id"]


def test_unexpected_failures_are_typed_without_logging_the_message(caplog) -> None:
    app = create_app(settings(), FakeDatabase())

    @app.get("/test-failure")
    def fail() -> None:
        raise RuntimeError("private request value")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-failure?private=request-value")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert '"route":"/test-failure"' in caplog.text
    assert '"exception_class":"RuntimeError"' in caplog.text
    assert "private request value" not in caplog.text
    assert "request-value" not in caplog.text


def test_act_source_ids_preserve_first_appearance_and_deduplicate() -> None:
    assert _ordered_source_ids(ACT_RECORD) == [SOURCE_ID]


def test_reader_endpoints_are_release_scoped_and_cacheable() -> None:
    database = FakeDatabase()
    with TestClient(create_app(settings(), database)) as client:
        acts = client.get("/v1/acts?limit=1")
        empty_page = client.get("/v1/acts?offset=10")
        act = client.get(f"/v1/acts/{ACT_ID}")
        source = client.get(f"/v1/sources/{SOURCE_ID}")
        cached_act = client.get(
            f"/v1/acts/{ACT_ID}",
            headers={"If-None-Match": act.headers["etag"]},
        )

    assert acts.json()["data"] == {
        "items": [ACT_SUMMARY],
        "pagination": {"offset": 0, "limit": 1, "total": 1},
    }
    assert empty_page.json()["data"] == {
        "items": [],
        "pagination": {"offset": 10, "limit": 50, "total": 1},
    }
    assert act.json()["data"] == {
        "act": ACT_RECORD,
        "sources": [SOURCE_RECORD],
    }
    assert source.json()["data"] == {"source": SOURCE_RECORD}
    assert cached_act.status_code == 304
    assert not cached_act.content
    assert cached_act.headers["openacts-corpus-release"] == RELEASE.release_tag
    assert database.active_release_calls == 5
    assert database.reader_calls == [
        ("list_acts", RELEASE.release_tag, 0, 1),
        ("list_acts", RELEASE.release_tag, 10, 50),
        ("get_act", RELEASE.release_tag, ACT_ID),
        ("get_source", RELEASE.release_tag, SOURCE_ID),
        ("get_act", RELEASE.release_tag, ACT_ID),
    ]


def test_reader_validation_and_missing_records_use_typed_errors() -> None:
    database = FakeDatabase()
    with TestClient(create_app(settings(), database)) as client:
        invalid_page = client.get("/v1/acts?limit=101")
        invalid_act = client.get("/v1/acts/INVALID")
        missing_act = client.get(f"/v1/acts/{MISSING_ACT_ID}")
        invalid_source = client.get("/v1/sources/sha256:not-a-hash")
        missing_source = client.get(f"/v1/sources/{MISSING_SOURCE_ID}")

    assert invalid_page.status_code == 400
    assert invalid_page.json()["error"]["code"] == "invalid_request"
    assert invalid_act.status_code == 400
    assert invalid_source.status_code == 400
    assert missing_act.status_code == 404
    assert missing_act.json()["error"]["code"] == "act_not_found"
    assert missing_source.status_code == 404
    assert missing_source.json()["error"]["code"] == "source_not_found"
    assert missing_source.headers["cache-control"] == "no-store"
    assert database.reader_calls == [
        ("get_act", RELEASE.release_tag, MISSING_ACT_ID),
        ("get_source", RELEASE.release_tag, MISSING_SOURCE_ID),
    ]
