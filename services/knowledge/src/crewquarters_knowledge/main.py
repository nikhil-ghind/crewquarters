"""Knowledge service application factory and entry point."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.types import Scope

from crewquarters_knowledge import api, embeddings
from crewquarters_knowledge.config import KnowledgeSettings, get_settings
from crewquarters_knowledge.embeddings import Embedder
from crewquarters_knowledge.limits import BodySizeLimit
from crewquarters_knowledge.metrics import KnowledgeMetrics
from crewquarters_knowledge.worker import KnowledgeWorker
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.logs import configure_logging

UPLOAD_OVERHEAD_BYTES = 64 * 1024


def _error(request: Request, code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "requestId": getattr(request.state, "request_id", None),
            "details": details,
        }
    }


def create_app(
    settings: KnowledgeSettings | None = None,
    *,
    embedder: Embedder | None = None,
    run_worker: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    embedder = embedder or embeddings.create(settings.embedding_mode, settings.embedding_cache_dir)
    engine = create_engine(settings.database_url, settings.db_pool_size)
    sessions = session_factory(engine)
    metrics = KnowledgeMetrics()
    worker = KnowledgeWorker(sessions, settings, embedder, metrics)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run_forever(stop)) if run_worker else None
        yield
        stop.set()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await engine.dispose()

    app = FastAPI(
        title="Crewquarters Knowledge",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url=None,  # no unauthenticated API map for agents
        docs_url=None,
        redoc_url=None,
    )
    app.state.knowledge = api.KnowledgeState(settings, sessions, embedder, metrics)

    def body_limit(scope: Scope) -> int:
        """Uploads may carry a whole document plus multipart framing; nothing else may."""
        if scope.get("method") == "POST" and str(scope.get("path", "")).endswith("/documents"):
            return settings.max_upload_bytes + UPLOAD_OVERHEAD_BYTES
        return settings.max_body_bytes

    app.add_middleware(BodySizeLimit, limit_for=body_limit)
    app.state.worker = worker

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        body = _error(request, exc.code, exc.message, exc.details)
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"path": "/" + "/".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ][:20]
        body = _error(request, "INVALID_INPUT", "The request is invalid.", {"errors": errors})
        return JSONResponse(body, status_code=422)

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if 8 <= len(incoming) <= 128 else uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        # The matched route template, never the raw path: IDs stay out of metrics.
        route = str(getattr(request.scope.get("route"), "path", "unmatched"))
        metrics.requests.labels(request.method, route, str(response.status_code)).inc()
        metrics.latency.labels(request.method, route).observe(time.perf_counter() - started)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        async with sessions() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ok", "embeddingProfile": embedder.profile}

    app.include_router(api.router)
    return app


def run() -> None:
    import os

    import uvicorn

    configure_logging("knowledge")
    uvicorn.run(
        "crewquarters_knowledge.main:create_app",
        factory=True,
        host=os.environ.get("CQ_KNOWLEDGE_HOST", "0.0.0.0"),  # noqa: S104 - container network
        port=int(os.environ.get("CQ_KNOWLEDGE_PORT", "8000")),
        access_log=False,
    )
