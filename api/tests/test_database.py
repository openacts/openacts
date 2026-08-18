import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from openacts_api.app import create_app
from openacts_api.config import Settings
from openacts_api.database import ProjectionDatabase

ACT_ID = "ng-federal-act-2023-37"
PROVISION_ID = f"{ACT_ID}:section-1"
RELEASE_TAG = "corpus-v0.0.0"
REVISION = "0" * 40
GOLDEN_SEARCHES = (
    ({"query": ACT_ID}, "exact_act_id", "act", ACT_ID),
    ({"query": "NDPA 2023"}, "exact_act_alias", "act", ACT_ID),
    (
        {"query": "section 1", "act_id": ACT_ID},
        "exact_provision_reference",
        "provision",
        PROVISION_ID,
    ),
)


@pytest.fixture(scope="module")
def postgres_client() -> Iterator[TestClient]:
    database_url = os.environ.get("OPENACTS_API_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("OPENACTS_API_TEST_DATABASE_URL is not configured")
    database = ProjectionDatabase(database_url)
    settings = Settings(
        database_url=database_url,
        application_revision=REVISION,
    )
    with TestClient(create_app(settings, database)) as client:
        yield client


def test_reader_and_search_use_the_real_projection(
    postgres_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready = postgres_client.get("/readyz")
    acts = postgres_client.get("/v1/acts")
    contents = postgres_client.get(f"/v1/acts/{ACT_ID}/contents")
    provision = postgres_client.get(f"/v1/provisions/{PROVISION_ID}")
    cached = postgres_client.get(
        f"/v1/acts/{ACT_ID}/contents",
        headers={"If-None-Match": contents.headers["etag"]},
    )

    assert ready.status_code == 200
    assert ready.json()["corpus_release"] == RELEASE_TAG
    assert acts.status_code == 200
    assert contents.status_code == 200
    assert provision.status_code == 200
    assert acts.json()["data"]["pagination"]["total"] == 2
    assert {item["act_id"] for item in acts.json()["data"]["items"]} == {
        "ng-federal-act-1999-constitution",
        ACT_ID,
    }

    outline = contents.json()["data"]["items"]
    section = next(item for item in outline if item["provision_id"] == PROVISION_ID)
    assert section["parent_provision_id"] == f"{ACT_ID}:part-1"
    assert section["has_children"] is True
    assert cached.status_code == 304
    assert cached.headers["openacts-corpus-release"] == RELEASE_TAG

    detail = provision.json()["data"]
    assert detail["provision"]["provision_id"] == PROVISION_ID
    assert detail["ancestors"][-1]["provision_id"] == f"{ACT_ID}:part-1"
    assert detail["navigation"]["next"]["provision_id"] == (
        f"{PROVISION_ID}.subsection-1"
    )
    assert detail["sources"]

    for request, match_kind, kind, resource_id in GOLDEN_SEARCHES:
        response = postgres_client.post("/v1/search", json=request)
        assert response.status_code == 200
        item = response.json()["data"]["items"][0]
        actual_id = (
            item["act"]["act_id"]
            if kind == "act"
            else item["provision"]["provision_id"]
        )
        assert response.headers["cache-control"] == "no-store"
        assert "etag" not in response.headers
        assert item["match_kind"] == match_kind
        assert item["kind"] == kind
        assert actual_id == resource_id

    lexical_query = "personal information"
    lexical = postgres_client.post(
        "/v1/search",
        json={"query": lexical_query, "act_id": ACT_ID},
    )
    assert lexical.status_code == 200
    assert lexical.json()["data"]["items"]
    assert all(
        item["match_kind"] == "lexical"
        for item in lexical.json()["data"]["items"]
    )
    assert lexical_query not in caplog.text

    invalid = postgres_client.get("/v1/acts/INVALID")
    missing = postgres_client.get("/v1/acts/ng-federal-act-2023-999")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "act_not_found"
