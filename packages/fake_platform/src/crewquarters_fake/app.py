"""FastAPI application factory for the fake platform."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from crewquarters_fake import admin, broker, control
from crewquarters_fake.errors import ApiError, api_error_handler, http_error_handler, validation_error_handler
from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.store import Store


def create_app(settings: FakeSettings | None = None) -> FastAPI:
    settings = settings or FakeSettings.from_env()
    app = FastAPI(title="Crewquarters fake platform", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.store = Store(settings)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.include_router(control.router)
    app.include_router(broker.router)
    app.include_router(admin.router)

    if settings.record_traffic:
        _record_traffic(app)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _record_traffic(app: FastAPI) -> None:
    """Record API and broker requests/responses (for contract conformance tests)."""

    @app.middleware("http")
    async def record(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if not path.startswith(("/internal/v1/sdk", "/api/v1")):
            return await call_next(request)
        body = await request.body()
        response = await call_next(request)
        entry: dict[str, Any] = {
            "method": request.method,
            "path": path,
            "query": dict(request.query_params),
            "requestBody": _json(body),
            "status": response.status_code,
            "responseBody": None,
        }
        store = app.state.store
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            store.traffic.append(entry)
            return response
        raw = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        entry["responseBody"] = _json(raw)
        store.traffic.append(entry)
        return Response(content=raw, status_code=response.status_code, headers=dict(response.headers))
