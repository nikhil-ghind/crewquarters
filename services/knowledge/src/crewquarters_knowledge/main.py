"""Knowledge service application factory and entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from crewquarters_knowledge import api, embeddings
from crewquarters_knowledge.config import KnowledgeSettings, get_settings
from crewquarters_knowledge.embeddings import PROFILES, Embedder
from crewquarters_knowledge.metrics import KnowledgeMetrics
from crewquarters_knowledge.worker import KnowledgeWorker
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.logs import configure_logging


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
    embedder = embedder or embeddings.create(settings.embedding_mode, settings.embedding_model_dir)
    engine = create_engine(settings.database_url, settings.db_pool_size)
    sessions = session_factory(engine)
    metrics = KnowledgeMetrics()
    worker = KnowledgeWorker(sessions, settings, embedder, metrics)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Load the model now, not on the first upload; if it is missing the service still
        # starts, reports why on /health/ready, and keeps looking for it.
        await worker.load_model()
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
        docs_url=None,
        redoc_url=None,
    )
    app.state.knowledge = api.KnowledgeState(settings, sessions, embedder, metrics)
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
    async def ready() -> JSONResponse:
        async with sessions() as db:
            await db.execute(text("SELECT 1"))
        model = {"embeddingProfile": embedder.profile, **(PROFILES.get(embedder.profile) or {})}
        if not embedder.ready:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "code": "EMBEDDING_MODEL_UNAVAILABLE",
                    "message": worker.model_error or "The embedding model is not loaded.",
                    **model,
                },
                status_code=503,
            )
        return JSONResponse({"status": "ok", **model})

    app.include_router(api.router)
    return app


def serve() -> None:
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


def fetch_model(model_dir: Path, base_url: str) -> int:
    """``cq-knowledge fetch-model``: 0 when the pinned files are in place and verified, 1 on
    a checksum mismatch, 2 when the download fails."""
    try:
        path = embeddings.fetch(model_dir, base_url=base_url)
    except embeddings.ChecksumMismatch as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, embeddings.ModelUnavailable) as exc:  # URLError is an OSError
        print(f"error: the embedding model could not be downloaded: {exc}", file=sys.stderr)
        return 2
    print(f"embedding model {embeddings.HF_REPO}@{embeddings.HF_REVISION} verified in {path}")
    return 0


def run(argv: list[str] | None = None) -> None:
    """``cq-knowledge [serve]`` runs the service; ``cq-knowledge fetch-model`` installs the
    pinned embedding model (run once, e.g. by an init container, before the service)."""
    parser = argparse.ArgumentParser(prog="cq-knowledge", description="Crewquarters knowledge")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the knowledge service (the default)")
    fetch = commands.add_parser(
        "fetch-model", help="download and verify the pinned embedding model (idempotent)"
    )
    fetch.add_argument(
        "--dir", type=Path, default=None, help="model directory (default: CQ_EMBEDDING_MODEL_DIR)"
    )
    fetch.add_argument("--base-url", default=embeddings.HF_BASE_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "fetch-model":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        model_dir = args.dir or KnowledgeSettings().embedding_model_dir
        sys.exit(fetch_model(model_dir, args.base_url))
    serve()
