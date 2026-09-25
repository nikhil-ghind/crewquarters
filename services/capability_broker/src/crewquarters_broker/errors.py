"""Error responses in the platform shape ``{"error": {code, message, requestId, details}}``.

Codes follow ``packages/contracts/broker-sdk.openapi.yaml``, which the SDK maps to its typed
exceptions: ``CAPABILITY_DENIED``/``PERMISSION_DENIED``, ``NEEDS_CONNECTION``,
``RUN_CANCELLED``, ``RATE_LIMITED``, ``INVALID_REQUEST``, ``OUTCOME_UNKNOWN``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from crewquarters_shared.errors import PlatformError


def _body(request: Request, code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
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
        request.app.state.broker.metrics.observe_error(exc.code)
        headers = {}
        if exc.status_code == 429 and "retryAfterSeconds" in exc.details:
            headers["Retry-After"] = str(exc.details["retryAfterSeconds"])
        return JSONResponse(
            _body(request, exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"path": "/" + "/".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ][:20]
        body = _body(request, "INVALID_REQUEST", "The request is invalid.", {"errors": errors})
        return JSONResponse(body, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(_body(request, code, str(exc.detail), {}), status_code=exc.status_code)


def unauthenticated(message: str = "A valid capability token is required.") -> PlatformError:
    return PlatformError("UNAUTHENTICATED", message, 401)


def capability_denied(capability: str) -> PlatformError:
    return PlatformError(
        "CAPABILITY_DENIED",
        f"This run is not allowed to use {capability}.",
        403,
        {"capability": capability},
    )


def permission_denied(message: str, **details: Any) -> PlatformError:
    return PlatformError("PERMISSION_DENIED", message, 403, details)


def needs_connection(provider: str, message: str) -> PlatformError:
    return PlatformError("NEEDS_CONNECTION", message, 409, {"provider": provider})


def provider_error(provider: str, status: int) -> PlatformError:
    """An upstream failure. Never echo the provider's body: it may contain personal data."""
    if status == 429:
        return PlatformError("RATE_LIMITED", f"{provider} rate limit reached.", 429)
    return PlatformError(
        "PROVIDER_ERROR", f"{provider} request failed.", 502, {"providerStatus": status}
    )
