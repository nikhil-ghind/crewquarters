"""Control API application factory and entry point."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from crewquarters_api import catalog, errors, security
from crewquarters_api.backups import BackupWorker
from crewquarters_api.deps import AppState
from crewquarters_api.gateway_client import GatewayClient
from crewquarters_api.routers import (
    agents,
    auth,
    chat,
    internal,
    knowledge,
    platform,
    runs,
    schedules,
    system,
)
from crewquarters_api.routers import connections as connections_router
from crewquarters_api.upstream import BrokerClient, ServiceClient
from crewquarters_shared.clients import (
    ConnectionStatusClient,
    FakeConnectionStatusClient,
    FakeModelStatusClient,
    ModelStatusClient,
)
from crewquarters_shared.config import Settings, get_settings
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.logs import configure_logging, install_recent_logs
from crewquarters_shared.metrics import ApiMetrics
from crewquarters_shared.runtime import DaemonRuntimeClient, RuntimeAdapter

log = logging.getLogger("crewquarters.api")

API_PREFIX = "/api/v1"
INTERNAL_PREFIX = "/internal/v1"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _status_clients(
    settings: Settings, broker: BrokerClient
) -> tuple[ModelStatusClient, ConnectionStatusClient]:
    """Model status comes from the model gateway and connection status from the capability
    broker. The connection fake reports every provider connected, so it is allowed only in
    the dev profile (``Settings.effective_broker_adapter``)."""
    models: ModelStatusClient
    if settings.model_gateway_adapter == "http":
        models = GatewayClient(
            settings.model_gateway_url,
            settings.internal_service_token.get_secret_value(),
            chat_token=settings.chat_client_token.get_secret_value(),
        )
    elif settings.model_gateway_adapter == "fake":
        models = FakeModelStatusClient()
    else:
        raise RuntimeError(f"Unknown CQ_MODEL_GATEWAY_ADAPTER={settings.model_gateway_adapter!r}")
    if settings.effective_broker_adapter() == "fake":
        return models, FakeConnectionStatusClient(settings.fake_connections)
    return models, broker


class BodySizeLimit:
    """Reject request bodies larger than ``limit`` bytes with 413, including chunked
    bodies that do not declare a Content-Length."""

    def __init__(
        self,
        app: ASGIApp,
        limit: int,
        overrides: list[tuple[re.Pattern[str], int]] | None = None,
    ) -> None:
        self.app = app
        self.limit = limit
        # (path pattern, limit) pairs for routes with their own limit (document uploads).
        self.overrides = overrides or []

    def _limit_for(self, scope: Scope) -> int:
        path = str(scope.get("path", ""))
        for pattern, limit in self.overrides:
            if scope.get("method") == "POST" and pattern.fullmatch(path):
                return limit
        return self.limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(send, limit)
            return
        seen = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal seen, too_large
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    too_large = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            # FastAPI converts body-read errors into a 400; replace it with our 413.
            if not too_large:
                await send(message)

        with contextlib.suppress(_BodyTooLarge):
            await self.app(scope, limited_receive, guarded_send)
        if too_large:
            await self._reject(send, limit)

    async def _reject(self, send: Send, limit: int) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"Request bodies are limited to {limit} bytes.",
                    "requestId": None,
                    "details": {},
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


def _runtime_client(settings: Settings) -> RuntimeAdapter | None:
    """The API reads host capacity (architecture, GPU) from the runtime daemon. In the
    dev profile the fake runtime lives in the worker, so there is nothing to ask."""
    if settings.runtime_adapter == "daemon":
        return DaemonRuntimeClient(
            settings.runtime_socket, settings.internal_service_token.get_secret_value()
        )
    return None


def _route_template(request: Request) -> str:
    """The matched route template (e.g. ``/api/v1/runs/{run_id}``), never the raw path,
    so IDs and query strings stay out of metrics and logs."""
    route: Any = request.scope.get("route")
    template = str(getattr(route, "path", "unmatched"))
    for prefix in (API_PREFIX, INTERNAL_PREFIX):
        if request.url.path.startswith(prefix) and not template.startswith(prefix):
            return prefix + template
    return template


def create_app(
    settings: Settings | None = None,
    *,
    transports: dict[str, httpx.AsyncBaseTransport] | None = None,
    run_backup_worker: bool = True,
) -> FastAPI:
    """``run_backup_worker=False`` (tests) leaves backup jobs queued.
    ``transports`` (tests only) replaces the HTTP transport of the ``broker``,
    ``knowledge`` or ``gateway`` service clients."""
    settings = settings or get_settings()
    transports = transports or {}
    install_recent_logs("control-api", settings.diagnostics_log_lines)
    token = settings.internal_service_token.get_secret_value()
    broker = BrokerClient(settings.broker_url, token, transport=transports.get("broker"))
    knowledge_client = ServiceClient(
        "knowledge", settings.knowledge_url, token, transport=transports.get("knowledge")
    )
    gateway_admin = ServiceClient(
        "model-gateway",
        settings.model_gateway_url,
        token,
        transport=transports.get("gateway"),
        timeout=60.0,
    )
    models, connections = _status_clients(settings, broker)
    runtime = _runtime_client(settings)
    metrics = ApiMetrics()
    engine = create_engine(settings.database_url, settings.db_pool_size)
    sessions = session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.catalog_dir:
            async with sessions() as db:
                loaded = await catalog.sync_directory(db, settings.catalog_dir)
                await db.commit()
            log.info("catalog synced: %s", ", ".join(loaded) or "no manifests")
        # Owner-triggered backups (system.backup jobs) run in this process: it is the one
        # holding the backup directory and the read-only document store.
        stop = asyncio.Event()
        backup_task = (
            asyncio.create_task(
                BackupWorker(sessions, settings, f"control-api:{uuid.uuid4().hex[:8]}").run_forever(
                    stop
                )
            )
            if settings.backup_dir is not None and run_backup_worker
            else None
        )
        yield
        stop.set()
        if backup_task is not None:
            await backup_task
        if runtime is not None:
            await runtime.close()
        if isinstance(models, GatewayClient):
            await models.close()
        for client in (broker, knowledge_client, gateway_admin):
            await client.close()
        await engine.dispose()

    app = FastAPI(
        title="Crewquarters Control API",
        version="0.1.0",
        description=(
            "Control plane for Crewquarters. JSON is camelCase. Errors use "
            "`{error: {code, message, requestId, details}}`. State-changing requests "
            "need the session cookie, an allowed Origin, and the `X-CSRF-Token` header. "
            "Mutating requests accept an `Idempotency-Key` header."
        ),
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.cq = AppState(
        settings=settings,
        sessions=sessions,
        models=models,
        connections=connections,
        auth_limiter=security.RateLimiter(settings.auth_rate_limit_per_minute),
        metrics=metrics,
        broker=broker,
        knowledge=knowledge_client,
        gateway_admin=gateway_admin,
        runtime=runtime,
    )
    app.state.engine = engine
    errors.install(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if 8 <= len(incoming) <= 128 else uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        route = _route_template(request)
        metrics.requests.labels(request.method, route, str(response.status_code)).inc()
        metrics.latency.labels(request.method, route).observe(elapsed)
        # Route templates only: query strings and path IDs never reach the log.
        log.info(
            "%s %s %s %.1fms",
            request.method,
            route,
            response.status_code,
            elapsed * 1000,
            extra={"request_id": request.state.request_id, "event": "http.request"},
        )
        response.headers["X-Request-ID"] = request.state.request_id
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.path.startswith(API_PREFIX) and not request.url.path.endswith("/docs"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    for router in (
        auth.router,
        agents.router,
        runs.router,
        schedules.router,
        platform.router,
        chat.router,
        connections_router.router,
        knowledge.router,
        system.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    app.include_router(internal.router, prefix=INTERNAL_PREFIX)
    app.add_middleware(
        BodySizeLimit,
        limit=settings.max_body_bytes,
        overrides=[(knowledge.UPLOAD_PATH, settings.max_upload_bytes + knowledge.MULTIPART_SLACK)],
    )
    return app


def run() -> None:
    import os

    import uvicorn

    configure_logging("control-api")
    uvicorn.run(
        "crewquarters_api.main:create_app",
        factory=True,
        host=os.environ.get("CQ_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("CQ_API_PORT", "8080")),
        proxy_headers=True,
        # Behind the Compose proxy the API port is unpublished and only the proxy and
        # platform services reach it, so Compose trusts their forwarded headers ("*").
        forwarded_allow_ips=os.environ.get("CQ_API_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        log_config=None,
        access_log=False,
    )
