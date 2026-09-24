"""FastAPI application factory for the fake platform."""

from __future__ import annotations

from fastapi import FastAPI
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

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    return app
