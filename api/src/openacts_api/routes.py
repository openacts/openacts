"""HTTP routes for the OpenActs API."""

from datetime import date
from hashlib import sha256
from typing import Annotated, Any, Literal
from unicodedata import normalize

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from openacts_api.config import Settings
from openacts_api.database import ActiveRelease, ProjectionReader

API_VERSION = "v1"
APPLICATION_REVISION_HEADER = "OpenActs-Application-Revision"
CORPUS_RELEASE_HEADER = "OpenActs-Corpus-Release"
NO_STORE = "no-store"
REVALIDATE = "public, max-age=0, must-revalidate"
ACT_ID_PATTERN = r"^[a-z]{2}(?:-[a-z0-9]+)+-[0-9]{4}-[a-z0-9][a-z0-9-]*$"
PROVISION_ID_PATTERN = (
    r"^[a-z]{2}(?:-[a-z0-9]+)+-[0-9]{4}-[a-z0-9][a-z0-9-]*:"
    r"[a-z0-9][a-z0-9.-]*(?:~[0-9]+)?$"
)
SOURCE_ID_PATTERN = r"^sha256:[0-9a-f]{64}$"

ActId = Annotated[str, Path(pattern=ACT_ID_PATTERN)]
ProvisionId = Annotated[str, Path(pattern=PROVISION_ID_PATTERN)]
SourceId = Annotated[str, Path(pattern=SOURCE_ID_PATTERN)]
SearchActId = Annotated[str, Field(pattern=ACT_ID_PATTERN)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]

router = APIRouter()


class ApiMeta(BaseModel):
    api_version: str
    application_revision: str
    corpus_release: str | None


class HealthResponse(BaseModel):
    status: str
    application_revision: str


class ReadinessResponse(BaseModel):
    status: str
    application_revision: str
    corpus_release: str


class MetaData(BaseModel):
    corpus_commit: str
    canonical_schema_versions: list[str]


class MetaResponse(BaseModel):
    meta: ApiMeta
    data: MetaData


class ActSummary(BaseModel):
    act_id: str
    official_title: str
    short_title: str | None
    year: int
    number: str | None
    citation: str | None
    text_kind: str
    status: str
    checked_through_date: date | None


class Pagination(BaseModel):
    offset: int
    limit: int
    total: int


class ActsData(BaseModel):
    items: list[ActSummary]
    pagination: Pagination


class ActsResponse(BaseModel):
    meta: ApiMeta
    data: ActsData


class ActDetailData(BaseModel):
    act: dict[str, Any]
    sources: list[dict[str, Any]]


class ActDetailResponse(BaseModel):
    meta: ApiMeta
    data: ActDetailData


class ProvisionOutline(BaseModel):
    provision_id: str
    parent_provision_id: str | None
    node_type: str
    display_label: str | None
    heading: str | None
    order: int
    sequence: int
    depth: int
    has_content: bool
    has_children: bool


class ActContentsData(BaseModel):
    items: list[ProvisionOutline]


class ActContentsResponse(BaseModel):
    meta: ApiMeta
    data: ActContentsData


class ProvisionSummary(BaseModel):
    provision_id: str
    act_id: str
    node_type: str
    display_label: str | None
    heading: str | None


class ProvisionNavigation(BaseModel):
    previous: ProvisionSummary | None
    next: ProvisionSummary | None


class CitationTarget(BaseModel):
    act: ActSummary
    provision: ProvisionSummary | None


class ProvisionCitation(BaseModel):
    citation: dict[str, Any]
    target: CitationTarget


class ProvisionDetailData(BaseModel):
    act: ActSummary
    provision: dict[str, Any]
    descendants: list[dict[str, Any]]
    ancestors: list[ProvisionSummary]
    navigation: ProvisionNavigation
    sources: list[dict[str, Any]]
    citations: list[ProvisionCitation]


class ProvisionDetailResponse(BaseModel):
    meta: ApiMeta
    data: ProvisionDetailData


class SourceData(BaseModel):
    source: dict[str, Any]


class SourceResponse(BaseModel):
    meta: ApiMeta
    data: SourceData


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    act_id: SearchActId | None = None
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        query = normalize("NFC", value).strip()
        if not 1 <= len(query) <= 256:
            raise ValueError("query must contain from 1 through 256 code points")
        return query


class SearchItem(BaseModel):
    kind: Literal["act", "provision"]
    match_kind: Literal[
        "exact_act_id",
        "exact_provision_id",
        "exact_act_citation",
        "exact_act_title",
        "exact_act_alias",
        "exact_provision_reference",
        "lexical",
    ]
    act: ActSummary
    provision: ProvisionSummary | None
    breadcrumb: list[ProvisionSummary]
    excerpt: str | None = Field(max_length=320)


class SearchData(BaseModel):
    items: list[SearchItem]


class SearchResponse(BaseModel):
    meta: ApiMeta
    data: SearchData


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> ProjectionReader:
    return request.app.state.database


SettingsDependency = Annotated[Settings, Depends(get_settings)]
DatabaseDependency = Annotated[ProjectionReader, Depends(get_database)]


def resolve_active_release(
    request: Request,
    database: DatabaseDependency,
) -> ActiveRelease:
    release = database.active_release()
    request.state.corpus_release = release.release_tag
    return release


ReleaseDependency = Annotated[ActiveRelease, Depends(resolve_active_release)]


def api_meta(settings: Settings, release: ActiveRelease) -> ApiMeta:
    return ApiMeta(
        api_version=API_VERSION,
        application_revision=settings.application_revision,
        corpus_release=release.release_tag,
    )


def corpus_response(request: Request, body: BaseModel) -> Response:
    content = body.model_dump_json().encode()
    etag = f'"{sha256(content).hexdigest()}"'
    headers = {"Cache-Control": REVALIDATE, "ETag": etag}
    validators = {
        value.strip().removeprefix("W/")
        for value in request.headers.get("If-None-Match", "").split(",")
    }
    if etag in validators or "*" in validators:
        return Response(status_code=304, headers=headers)
    return Response(content=content, media_type="application/json", headers=headers)


@router.get("/healthz", response_model=HealthResponse)
def health(response: Response, settings: SettingsDependency) -> HealthResponse:
    response.headers["Cache-Control"] = NO_STORE
    return HealthResponse(
        status="ok",
        application_revision=settings.application_revision,
    )


@router.get("/readyz", response_model=ReadinessResponse)
def readiness(
    response: Response,
    settings: SettingsDependency,
    release: ReleaseDependency,
) -> ReadinessResponse:
    response.headers["Cache-Control"] = NO_STORE
    return ReadinessResponse(
        status="ready",
        application_revision=settings.application_revision,
        corpus_release=release.release_tag,
    )


@router.get("/v1/meta", response_model=MetaResponse)
def metadata(
    request: Request,
    settings: SettingsDependency,
    release: ReleaseDependency,
) -> Response:
    body = MetaResponse(
        meta=api_meta(settings, release),
        data=MetaData(
            corpus_commit=release.commit_sha,
            canonical_schema_versions=list(release.canonical_schema_versions),
        ),
    )
    return corpus_response(request, body)


@router.get("/v1/acts", response_model=ActsResponse)
def list_acts(
    request: Request,
    settings: SettingsDependency,
    database: DatabaseDependency,
    release: ReleaseDependency,
    offset: Offset = 0,
    limit: Limit = 50,
) -> Response:
    rows, total = database.list_acts(
        release.release_tag,
        offset,
        limit,
    )
    body = ActsResponse(
        meta=api_meta(settings, release),
        data=ActsData(
            items=[ActSummary.model_validate(row) for row in rows],
            pagination=Pagination(offset=offset, limit=limit, total=total),
        ),
    )
    return corpus_response(request, body)


@router.get("/v1/acts/{act_id}", response_model=ActDetailResponse)
def act_detail(
    request: Request,
    act_id: ActId,
    settings: SettingsDependency,
    database: DatabaseDependency,
    release: ReleaseDependency,
) -> Response:
    result = database.get_act(release.release_tag, act_id)
    if result is None:
        raise ApiError(
            404,
            "act_not_found",
            "Act not found.",
            retryable=False,
        )
    act, sources = result
    return corpus_response(
        request,
        ActDetailResponse(
            meta=api_meta(settings, release),
            data=ActDetailData(act=act, sources=sources),
        ),
    )


@router.get("/v1/acts/{act_id}/contents", response_model=ActContentsResponse)
def act_contents(
    request: Request,
    act_id: ActId,
    settings: SettingsDependency,
    database: DatabaseDependency,
    release: ReleaseDependency,
) -> Response:
    rows = database.get_act_contents(release.release_tag, act_id)
    if rows is None:
        raise ApiError(
            404,
            "act_not_found",
            "Act not found.",
            retryable=False,
        )
    return corpus_response(
        request,
        ActContentsResponse(
            meta=api_meta(settings, release),
            data=ActContentsData(
                items=[ProvisionOutline.model_validate(row) for row in rows]
            ),
        ),
    )


@router.get("/v1/provisions/{provision_id}", response_model=ProvisionDetailResponse)
def provision_detail(
    request: Request,
    provision_id: ProvisionId,
    settings: SettingsDependency,
    database: DatabaseDependency,
    release: ReleaseDependency,
) -> Response:
    result = database.get_provision(release.release_tag, provision_id)
    if result is None:
        raise ApiError(
            404,
            "provision_not_found",
            "Provision not found.",
            retryable=False,
        )
    return corpus_response(
        request,
        ProvisionDetailResponse(
            meta=api_meta(settings, release),
            data=ProvisionDetailData.model_validate(result),
        ),
    )


@router.get("/v1/sources/{source_id}", response_model=SourceResponse)
def source_detail(
    request: Request,
    source_id: SourceId,
    settings: SettingsDependency,
    database: DatabaseDependency,
    release: ReleaseDependency,
) -> Response:
    source = database.get_source(release.release_tag, source_id)
    if source is None:
        raise ApiError(
            404,
            "source_not_found",
            "Source not found.",
            retryable=False,
        )
    return corpus_response(
        request,
        SourceResponse(
            meta=api_meta(settings, release),
            data=SourceData(source=source),
        ),
    )


@router.post("/v1/search", response_model=SearchResponse)
def search(
    body: SearchRequest,
    response: Response,
    settings: SettingsDependency,
    database: DatabaseDependency,
    release: ReleaseDependency,
) -> SearchResponse:
    items = database.search(
        release.release_tag,
        body.query,
        body.act_id,
        body.limit,
    )
    if items is None:
        raise ApiError(
            404,
            "act_not_found",
            "Act not found.",
            retryable=False,
        )
    response.headers["Cache-Control"] = NO_STORE
    return SearchResponse(
        meta=api_meta(settings, release),
        data=SearchData(items=[SearchItem.model_validate(item) for item in items]),
    )
