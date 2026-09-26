"""Capability broker application factory and entry point."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from crewquarters_broker import agent_api, callbacks, connections, errors, fakes, voice_routes
from crewquarters_broker.config import BrokerSettings, get_settings
from crewquarters_broker.deps import BrokerState, internal_auth
from crewquarters_broker.github import GitHubConnector
from crewquarters_broker.google import GoogleConnector
from crewquarters_broker.internal import InternalClient
from crewquarters_broker.metrics import BrokerMetrics, MeteredTransport
from crewquarters_broker.twilio import TelephonyService
from crewquarters_broker.voice import VoiceService
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.logs import configure_logging
from crewquarters_shared.metrics import CONTENT_TYPE

PROVIDER_TIMEOUT_SECONDS = 20.0
# A local model may cold-start before its first token (PLAN.md section 8). Longer than the
# gateway's worst case (CQ_GATEWAY_WAIT_READY_SECONDS 900 + CQ_GATEWAY_REQUEST_TIMEOUT_SECONDS
# 300) and shorter than the SDK's LLM_TIMEOUT_SECONDS (docs/model-gateway.md, "Timeouts").
GATEWAY_TIMEOUT_SECONDS = 1260.0


def create_app(
    settings: BrokerSettings | None = None,
    *,
    provider_transport: httpx.AsyncBaseTransport | None = None,
    control_transport: httpx.AsyncBaseTransport | None = None,
    knowledge_transport: httpx.AsyncBaseTransport | None = None,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Transports are injectable for tests. In fake provider mode, Google, Twilio and GitHub are
    served by :mod:`crewquarters_broker.fakes` unless a transport is given."""
    settings = settings or get_settings()
    keyring = settings.keyring()
    engine = create_engine(settings.database_url, settings.db_pool_size)
    sessions = session_factory(engine)
    token = settings.internal_service_token.get_secret_value()
    fake = settings.provider_mode == "fake"
    if fake and provider_transport is None:
        provider_transport = fakes.transport(
            fakes.FakeGoogle(), fakes.FakeTwilio(), fakes.FakeGitHub()
        )
    metrics = BrokerMetrics()
    http = httpx.AsyncClient(
        timeout=PROVIDER_TIMEOUT_SECONDS,
        transport=MeteredTransport(provider_transport or httpx.AsyncHTTPTransport(), metrics),
    )
    control = InternalClient(settings.control_api_url, token, control_transport)
    knowledge = InternalClient(settings.knowledge_url, token, knowledge_transport)
    gateway = httpx.AsyncClient(
        base_url=f"{settings.model_gateway_url.rstrip('/')}/internal/v1",
        headers={"authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(GATEWAY_TIMEOUT_SECONDS, connect=5.0),
        transport=gateway_transport,
    )
    telephony = TelephonyService(settings, keyring, sessions, http, metrics)
    voice = VoiceService(settings, telephony, http, sessions, gateway_transport)
    simulations: set[asyncio.Task[None]] = set()
    if fake:
        telephony.on_created, simulations = fakes.call_simulator(telephony)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # Let simulated callbacks finish: cancelling one mid-statement can leave its
        # connection inside a transaction that blocks other writers.
        await asyncio.gather(*simulations, return_exceptions=True)
        await http.aclose()
        await control.close()
        await knowledge.close()
        await gateway.aclose()
        await voice.close()
        await engine.dispose()

    app = FastAPI(
        title="Crewquarters Capability Broker",
        version="0.1.0",
        description=(
            "SDK API (`/internal/v1/sdk`, capability token), connection management for the "
            "control API (`/internal/v1`, service token), and the public OAuth/Twilio "
            "callback paths."
        ),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.broker = BrokerState(
        settings=settings,
        keyring=keyring,
        sessions=sessions,
        control=control,
        knowledge=knowledge,
        gateway=gateway,
        google=GoogleConnector(settings, keyring, sessions, http, metrics),
        telephony=telephony,
        voice=voice,
        github=GitHubConnector(settings, http),
        metrics=metrics,
    )
    errors.install(app)

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
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        async with sessions() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get(
        "/internal/v1/metrics",
        dependencies=[Depends(internal_auth)],
        response_class=PlainTextResponse,
        include_in_schema=False,
    )
    async def metrics_text() -> PlainTextResponse:
        return PlainTextResponse(metrics.render(), media_type=CONTENT_TYPE)

    app.include_router(agent_api.router)
    app.include_router(connections.router)
    app.include_router(callbacks.router)
    app.include_router(voice_routes.internal)
    app.include_router(voice_routes.public)
    return app


def run() -> None:
    import os

    import uvicorn

    configure_logging("capability-broker")
    uvicorn.run(
        "crewquarters_broker.main:create_app",
        factory=True,
        host=os.environ.get("CQ_BROKER_HOST", "0.0.0.0"),  # noqa: S104 - container network
        port=int(os.environ.get("CQ_BROKER_PORT", "8000")),
        access_log=False,
    )
