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
PROVISION_ID = f"{ACT_ID}:section-1"
MISSING_PROVISION_ID = f"{ACT_ID}:section-999"
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
ACT_CONTENTS: list[dict[str, Any]] = [
    {
        "provision_id": f"{ACT_ID}:part-1",
        "parent_provision_id": None,
        "node_type": "part",
        "display_label": "PART I",
        "heading": "OBJECTIVES AND APPLICATION",
        "order": 1,
        "sequence": 1,
        "depth": 0,
        "has_content": False,
        "has_children": True,
    },
    {
        "provision_id": f"{ACT_ID}:section-1",
        "parent_provision_id": f"{ACT_ID}:part-1",
        "node_type": "section",
        "display_label": "1.",
        "heading": "Objectives",
        "order": 1,
        "sequence": 2,
        "depth": 1,
        "has_content": True,
        "has_children": False,
    },
]
PARENT_SUMMARY: dict[str, Any] = {
    "provision_id": f"{ACT_ID}:part-1",
    "act_id": ACT_ID,
    "node_type": "part",
    "display_label": "PART I",
    "heading": "OBJECTIVES AND APPLICATION",
}
PROVISION_SUMMARY: dict[str, Any] = {
    "provision_id": PROVISION_ID,
    "act_id": ACT_ID,
    "node_type": "section",
    "display_label": "1.",
    "heading": "Objectives",
}
DESCENDANT_SUMMARY: dict[str, Any] = {
    "provision_id": f"{PROVISION_ID}.subsection-1",
    "act_id": ACT_ID,
    "node_type": "subsection",
    "display_label": "(1)",
    "heading": None,
}
TARGET_PROVISION_SUMMARY: dict[str, Any] = {
    "provision_id": f"{ACT_ID}:section-2",
    "act_id": ACT_ID,
    "node_type": "section",
    "display_label": "2.",
    "heading": "Application of the Act",
}
PROVISION_RECORD: dict[str, Any] = {
    "schema_version": "0.1.0",
    "record_type": "provision",
    "provision_id": PROVISION_ID,
    "node_type": "section",
    "display_label": "1.",
    "heading": "Objectives",
    "parent_provision_id": PARENT_SUMMARY["provision_id"],
    "order": 1,
    "source_spans": [{"source_id": SOURCE_ID, "pdf_page": 7}],
    "content_blocks": [],
    "text_fidelity": "single_reviewed",
}
DESCENDANT_RECORD: dict[str, Any] = {
    "schema_version": "0.1.0",
    "record_type": "provision",
    "provision_id": DESCENDANT_SUMMARY["provision_id"],
    "node_type": "subsection",
    "display_label": "(1)",
    "heading": None,
    "parent_provision_id": PROVISION_ID,
    "order": 1,
    "source_spans": [{"source_id": SOURCE_ID, "pdf_page": 7}],
    "content_blocks": [],
    "text_fidelity": "single_reviewed",
}
CITATION_RECORD: dict[str, Any] = {
    "schema_version": "0.1.0",
    "record_type": "citation",
    "citation_id": f"citation:{ACT_ID}:000001",
    "source_provision_id": PROVISION_ID,
    "source_block_id": "block-1",
    "text_range": {"start": 0, "end": 9},
    "target": {
        "act_id": ACT_ID,
        "provision_id": TARGET_PROVISION_SUMMARY["provision_id"],
    },
}
PROVISION_DETAIL: dict[str, Any] = {
    "act": ACT_SUMMARY,
    "provision": PROVISION_RECORD,
    "descendants": [DESCENDANT_RECORD],
    "ancestors": [PARENT_SUMMARY],
    "navigation": {
        "previous": PARENT_SUMMARY,
        "next": DESCENDANT_SUMMARY,
    },
    "sources": [SOURCE_RECORD],
    "citations": [
        {
            "citation": CITATION_RECORD,
            "target": {
                "act": ACT_SUMMARY,
                "provision": TARGET_PROVISION_SUMMARY,
            },
        }
    ],
}
SEARCH_ITEMS: list[dict[str, Any]] = [
    {
        "kind": "provision",
        "match_kind": "exact_provision_reference",
        "act": ACT_SUMMARY,
        "provision": PROVISION_SUMMARY,
        "breadcrumb": [PARENT_SUMMARY],
        "excerpt": "Objectives",
    }
]


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

    def get_act_contents(
        self, release_tag: str, act_id: str
    ) -> list[dict[str, Any]] | None:
        self.reader_calls.append(("get_act_contents", release_tag, act_id))
        return ACT_CONTENTS if act_id == ACT_ID else None

    def get_provision(
        self, release_tag: str, provision_id: str
    ) -> dict[str, Any] | None:
        self.reader_calls.append(("get_provision", release_tag, provision_id))
        return PROVISION_DETAIL if provision_id == PROVISION_ID else None

    def search(
        self,
        release_tag: str,
        query: str,
        act_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        self.reader_calls.append(("search", release_tag, query, act_id, limit))
        if act_id == MISSING_ACT_ID:
            return None
        return SEARCH_ITEMS if query == "Section 1" else []


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
    class FailingSearchDatabase(FakeDatabase):
        def search(
            self,
            release_tag: str,
            query: str,
            act_id: str | None,
            limit: int,
        ) -> list[dict[str, Any]] | None:
            raise RuntimeError(query)

    private_query = "private search value"
    app = create_app(settings(), FailingSearchDatabase())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/v1/search", json={"query": private_query})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert '"route":"/v1/search"' in caplog.text
    assert '"exception_class":"RuntimeError"' in caplog.text
    assert private_query not in caplog.text


def test_act_source_ids_preserve_first_appearance_and_deduplicate() -> None:
    assert _ordered_source_ids(ACT_RECORD) == [SOURCE_ID]


def test_reader_endpoints_are_release_scoped_and_cacheable() -> None:
    database = FakeDatabase()
    with TestClient(create_app(settings(), database)) as client:
        acts = client.get("/v1/acts?limit=1")
        empty_page = client.get("/v1/acts?offset=10")
        act = client.get(f"/v1/acts/{ACT_ID}")
        contents = client.get(f"/v1/acts/{ACT_ID}/contents")
        provision = client.get(f"/v1/provisions/{PROVISION_ID}")
        source = client.get(f"/v1/sources/{SOURCE_ID}")
        cached_contents = client.get(
            f"/v1/acts/{ACT_ID}/contents",
            headers={"If-None-Match": contents.headers["etag"]},
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
    assert contents.json()["data"] == {"items": ACT_CONTENTS}
    assert provision.json()["data"] == PROVISION_DETAIL
    assert source.json()["data"] == {"source": SOURCE_RECORD}
    assert cached_contents.status_code == 304
    assert not cached_contents.content
    assert cached_contents.headers["openacts-corpus-release"] == RELEASE.release_tag
    assert database.active_release_calls == 7
    assert database.reader_calls == [
        ("list_acts", RELEASE.release_tag, 0, 1),
        ("list_acts", RELEASE.release_tag, 10, 50),
        ("get_act", RELEASE.release_tag, ACT_ID),
        ("get_act_contents", RELEASE.release_tag, ACT_ID),
        ("get_provision", RELEASE.release_tag, PROVISION_ID),
        ("get_source", RELEASE.release_tag, SOURCE_ID),
        ("get_act_contents", RELEASE.release_tag, ACT_ID),
    ]


def test_reader_validation_and_missing_records_use_typed_errors() -> None:
    database = FakeDatabase()
    with TestClient(create_app(settings(), database)) as client:
        invalid_page = client.get("/v1/acts?limit=101")
        invalid_act = client.get("/v1/acts/INVALID")
        invalid_contents = client.get("/v1/acts/INVALID/contents")
        invalid_provision = client.get("/v1/provisions/INVALID")
        missing_act = client.get(f"/v1/acts/{MISSING_ACT_ID}")
        missing_contents = client.get(f"/v1/acts/{MISSING_ACT_ID}/contents")
        missing_provision = client.get(f"/v1/provisions/{MISSING_PROVISION_ID}")
        invalid_source = client.get("/v1/sources/sha256:not-a-hash")
        missing_source = client.get(f"/v1/sources/{MISSING_SOURCE_ID}")

    assert invalid_page.status_code == 400
    assert invalid_page.json()["error"]["code"] == "invalid_request"
    assert invalid_act.status_code == 400
    assert invalid_contents.status_code == 400
    assert invalid_provision.status_code == 400
    assert invalid_source.status_code == 400
    assert missing_act.status_code == 404
    assert missing_act.json()["error"]["code"] == "act_not_found"
    assert missing_contents.status_code == 404
    assert missing_contents.json()["error"]["code"] == "act_not_found"
    assert missing_provision.status_code == 404
    assert missing_provision.json()["error"]["code"] == "provision_not_found"
    assert missing_source.status_code == 404
    assert missing_source.json()["error"]["code"] == "source_not_found"
    assert missing_source.headers["cache-control"] == "no-store"
    assert database.reader_calls == [
        ("get_act", RELEASE.release_tag, MISSING_ACT_ID),
        ("get_act_contents", RELEASE.release_tag, MISSING_ACT_ID),
        ("get_provision", RELEASE.release_tag, MISSING_PROVISION_ID),
        ("get_source", RELEASE.release_tag, MISSING_SOURCE_ID),
    ]


def test_search_is_private_bounded_and_release_scoped() -> None:
    database = FakeDatabase()
    with TestClient(create_app(settings(), database)) as client:
        result = client.post(
            "/v1/search",
            json={"query": "  Section 1  ", "act_id": ACT_ID, "limit": 1},
        )
        empty = client.post("/v1/search", json={"query": "no match"})
        normalized = client.post("/v1/search", json={"query": " Cafe\u0301 "})
        missing_act = client.post(
            "/v1/search",
            json={"query": "section 1", "act_id": MISSING_ACT_ID},
        )
        invalid_requests = [
            client.post("/v1/search", json={"query": "   "}),
            client.post("/v1/search", json={"query": "valid", "limit": 51}),
            client.post("/v1/search", json={"query": "valid", "unknown": True}),
            client.post("/v1/search", json={"query": "valid", "act_id": "INVALID"}),
            client.post("/v1/search", json={"query": "x" * 257}),
        ]

    assert result.status_code == 200
    assert result.json()["data"] == {"items": SEARCH_ITEMS}
    assert result.headers["cache-control"] == "no-store"
    assert "etag" not in result.headers
    assert result.headers["openacts-corpus-release"] == RELEASE.release_tag
    assert empty.status_code == 200
    assert empty.json()["data"] == {"items": []}
    assert normalized.status_code == 200
    assert missing_act.status_code == 404
    assert missing_act.json()["error"]["code"] == "act_not_found"
    assert all(response.status_code == 400 for response in invalid_requests)
    assert database.reader_calls == [
        ("search", RELEASE.release_tag, "Section 1", ACT_ID, 1),
        ("search", RELEASE.release_tag, "no match", None, 20),
        ("search", RELEASE.release_tag, "Café", None, 20),
        ("search", RELEASE.release_tag, "section 1", MISSING_ACT_ID, 20),
    ]
