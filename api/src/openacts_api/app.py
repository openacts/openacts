"""FastAPI transport for projection health and release metadata."""

import json
import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openacts_api.config import Settings
from openacts_api.database import (
    ActiveRelease,
    ProjectionDatabase,
    ProjectionUnavailable,
)

API_VERSION = "v1"
APPLICATION_REVISION_HEADER = "OpenActs-Application-Revision"
CORPUS_RELEASE_HEADER = "OpenActs-Corpus-Release"
NO_STORE = "no-store"
REVALIDATE = "public, max-age=0, must-revalidate"

logger = logging.getLogger("openacts_api")


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


def create_app(
    settings: Settings | None = None,
    database: ProjectionDatabase | None = None,
) -> FastAPI:
    active_settings = settings or Settings.from_env()
    active_database = database or ProjectionDatabase(active_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        active_database.open()
        try:
            yield
        finally:
            active_database.close()

    app = FastAPI(title="OpenActs API", version=API_VERSION, lifespan=lifespan)

    if active_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(active_settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "If-None-Match"],
            expose_headers=[
                "ETag",
                APPLICATION_REVISION_HEADER,
                CORPUS_RELEASE_HEADER,
            ],
        )

    @app.middleware("http")
    async def response_identity(request: Request, call_next):
        request.state.request_id = uuid4().hex
        request.state.corpus_release = None
        response = await call_next(request)
        response.headers[APPLICATION_REVISION_HEADER] = (
            active_settings.application_revision
        )
        if request.state.corpus_release:
            response.headers[CORPUS_RELEASE_HEADER] = request.state.corpus_release
        return response

    def error_response(request: Request, error: ApiError) -> JSONResponse:
        corpus_release = request.state.corpus_release
        return JSONResponse(
            status_code=error.status,
            headers={"Cache-Control": NO_STORE},
            content={
                "meta": {
                    "api_version": API_VERSION,
                    "application_revision": active_settings.application_revision,
                    "corpus_release": corpus_release,
                },
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                    "request_id": request.state.request_id,
                },
            },
        )

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, _: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            request,
            ApiError(
                400,
                "invalid_request",
                "Request is invalid.",
                retryable=False,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, error: Exception
    ) -> JSONResponse:
        route = request.scope.get("route")
        logger.error(
            json.dumps(
                {
                    "event": "internal_error",
                    "request_id": request.state.request_id,
                    "route": getattr(route, "path", "unmatched"),
                    "application_revision": active_settings.application_revision,
                    "corpus_release": request.state.corpus_release,
                    "exception_class": type(error).__name__,
                    # Frame metadata locates the fault without serializing an
                    # exception message or source line that may contain private
                    # request data.
                    "stack": [
                        {
                            "file": Path(frame.filename).name,
                            "function": frame.name,
                            "line": frame.lineno,
                        }
                        for frame in traceback.extract_tb(error.__traceback__)
                    ],
                },
                separators=(",", ":"),
            )
        )
        # Unknown failures stay retryable because a wasted retry is safer than a
        # wrongly permanent failure.
        return error_response(
            request,
            ApiError(
                500,
                "internal_error",
                "An internal error occurred.",
                retryable=True,
            ),
        )

    def resolve_active_release(request: Request) -> ActiveRelease:
        try:
            release = active_database.active_release()
        except ProjectionUnavailable as exc:
            raise ApiError(
                503,
                "projection_unavailable",
                "Corpus projection is unavailable.",
                retryable=True,
            ) from exc
        request.state.corpus_release = release.release_tag
        return release

    Release = Annotated[ActiveRelease, Depends(resolve_active_release)]

    @app.get("/healthz", response_model=HealthResponse)
    def health(response: Response) -> HealthResponse:
        response.headers["Cache-Control"] = NO_STORE
        return HealthResponse(
            status="ok",
            application_revision=active_settings.application_revision,
        )

    @app.get("/readyz", response_model=ReadinessResponse)
    def readiness(response: Response, release: Release) -> ReadinessResponse:
        response.headers["Cache-Control"] = NO_STORE
        return ReadinessResponse(
            status="ready",
            application_revision=active_settings.application_revision,
            corpus_release=release.release_tag,
        )

    @app.get("/v1/meta", response_model=MetaResponse)
    def metadata(
        request: Request, response: Response, release: Release
    ) -> MetaResponse | Response:
        body = MetaResponse(
            meta=ApiMeta(
                api_version=API_VERSION,
                application_revision=active_settings.application_revision,
                corpus_release=release.release_tag,
            ),
            data=MetaData(
                corpus_commit=release.commit_sha,
                canonical_schema_versions=list(release.canonical_schema_versions),
            ),
        )
        etag = f'"{sha256(body.model_dump_json().encode()).hexdigest()}"'
        headers = {"Cache-Control": REVALIDATE, "ETag": etag}
        validators = {
            value.strip()
            for value in request.headers.get("If-None-Match", "").split(",")
        }
        if etag in validators or "*" in validators:
            return Response(status_code=304, headers=headers)
        response.headers.update(headers)
        return body

    return app
