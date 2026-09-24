"""Uniform error responses: ``{"error": {code, message, requestId, details}}``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from crewquarters_shared.errors import PlatformError

log = logging.getLogger(__name__)

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


def error_body(
    request: Request, code: str, message: str, details: dict[str, Any]
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "requestId": getattr(request.state, "request_id", None),
            "details": details,
        }
    }


def install(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        headers = {}
        if exc.status_code == 429 and "retryAfterSeconds" in exc.details:
            headers["Retry-After"] = str(exc.details["retryAfterSeconds"])
        return JSONResponse(
            error_body(request, exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"path": "/" + "/".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ][:20]
        return JSONResponse(
            error_body(request, "VALIDATION_FAILED", "The request is invalid.", {"errors": errors}),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            error_body(request, code, str(exc.detail), {}), status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled error", extra={"request_id": getattr(request.state, "request_id", None)}
        )
        return JSONResponse(
            error_body(request, "INTERNAL", "An unexpected error occurred.", {}), status_code=500
        )
