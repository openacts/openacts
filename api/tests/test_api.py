from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from openacts_api.app import create_app
from openacts_api.config import Settings
from openacts_api.database import ActiveRelease, ProjectionUnavailable

REVISION = "a" * 40
RELEASE = ActiveRelease(
    release_tag="corpus-v0.0.0",
    commit_sha="b" * 40,
    canonical_schema_versions=("0.1.0",),
)


class FakeDatabase:
    def __init__(self, release: ActiveRelease | None = RELEASE) -> None:
        self.release = release
        self.active_release_calls = 0

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def active_release(self) -> ActiveRelease:
        self.active_release_calls += 1
        if self.release is None:
            raise ProjectionUnavailable
        return self.release


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
    assert preflight.status_code == 200
    assert "if-none-match" in preflight.headers["access-control-allow-headers"].lower()
    assert database.active_release_calls == 3


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
