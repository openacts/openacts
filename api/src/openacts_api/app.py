"""FastAPI application composition for OpenActs."""

import json
import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from openacts_api.config import Settings
from openacts_api.database import (
    ProjectionDatabase,
    ProjectionReader,
    ProjectionUnavailable,
)
from openacts_api.routes import (
    API_VERSION,
    APPLICATION_REVISION_HEADER,
    CORPUS_RELEASE_HEADER,
    NO_STORE,
    ApiError,
    router,
)

logger = logging.getLogger("openacts_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database = app.state.database
    database.open()
    try:
        yield
    finally:
        database.close()


async def response_identity(request: Request, call_next):
    request.state.request_id = uuid4().hex
    request.state.corpus_release = None
    response = await call_next(request)
    settings = request.app.state.settings
    response.headers[APPLICATION_REVISION_HEADER] = settings.application_revision
    if request.state.corpus_release:
        response.headers[CORPUS_RELEASE_HEADER] = request.state.corpus_release
    return response


def error_response(request: Request, error: ApiError) -> JSONResponse:
    settings = request.app.state.settings
    return JSONResponse(
        status_code=error.status,
        headers={"Cache-Control": NO_STORE},
        content={
            "meta": {
                "api_version": API_VERSION,
                "application_revision": settings.application_revision,
                "corpus_release": request.state.corpus_release,
            },
            "error": {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "request_id": request.state.request_id,
            },
        },
    )


async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
    return error_response(request, error)


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


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    route = request.scope.get("route")
    settings = request.app.state.settings
    logger.error(
        json.dumps(
            {
                "event": "internal_error",
                "request_id": request.state.request_id,
                "route": getattr(route, "path", "unmatched"),
                "application_revision": settings.application_revision,
                "corpus_release": request.state.corpus_release,
                "exception_class": type(error).__name__,
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
    return error_response(
        request,
        ApiError(
            500,
            "internal_error",
            "An internal error occurred.",
            retryable=True,
        ),
    )


async def handle_projection_unavailable(
    request: Request, _: ProjectionUnavailable
) -> JSONResponse:
    return error_response(
        request,
        ApiError(
            503,
            "projection_unavailable",
            "Corpus projection is unavailable.",
            retryable=True,
        ),
    )


def create_app(
    settings: Settings | None = None,
    database: ProjectionReader | None = None,
) -> FastAPI:
    active_settings = settings or Settings.from_env()
    active_database = database or ProjectionDatabase(active_settings.database_url)
    app = FastAPI(title="OpenActs API", version=API_VERSION, lifespan=lifespan)
    app.state.settings = active_settings
    app.state.database = active_database

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

    app.middleware("http")(response_identity)
    app.exception_handler(ApiError)(handle_api_error)
    app.exception_handler(RequestValidationError)(handle_validation_error)
    app.exception_handler(Exception)(handle_unexpected_error)
    app.exception_handler(ProjectionUnavailable)(handle_projection_unavailable)
    app.include_router(router)
    return app
