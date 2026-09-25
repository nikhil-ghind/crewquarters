"""Capability broker application factory and entry point."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from crewquarters_broker import agent_api, callbacks, connections, errors, fakes
from crewquarters_broker.config import BrokerSettings, get_settings
from crewquarters_broker.deps import BrokerState
from crewquarters_broker.google import GoogleConnector
from crewquarters_broker.internal import InternalClient
from crewquarters_broker.twilio import TelephonyService
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.logs import configure_logging

PROVIDER_TIMEOUT_SECONDS = 20.0


def create_app(
    settings: BrokerSettings | None = None,
    *,
    provider_transport: httpx.AsyncBaseTransport | None = None,
    control_transport: httpx.AsyncBaseTransport | None = None,
    knowledge_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Transports are injectable for tests. In fake provider mode, Google and Twilio are
    served by :mod:`crewquarters_broker.fakes` unless a transport is given."""
    settings = settings or get_settings()
    keyring = settings.keyring()
    engine = create_engine(settings.database_url, settings.db_pool_size)
    sessions = session_factory(engine)
    token = settings.internal_service_token.get_secret_value()
    fake = settings.provider_mode == "fake"
    if fake and provider_transport is None:
        provider_transport = fakes.transport(fakes.FakeGoogle(), fakes.FakeTwilio())
    http = httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SECONDS, transport=provider_transport)
    control = InternalClient(settings.control_api_url, token, control_transport)
    knowledge = InternalClient(settings.knowledge_url, token, knowledge_transport)
    telephony = TelephonyService(settings, keyring, sessions, http)
    if fake:
        telephony.on_created, _ = fakes.call_simulator(telephony)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await http.aclose()
        await control.close()
        await knowledge.close()
        await engine.dispose()

    app = FastAPI(
        title="Crewquarters Capability Broker",
        version="0.1.0",
        description=(
            "Agent API (`/agent/v1`, capability token), connection management for the "
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
        google=GoogleConnector(settings, keyring, sessions, http),
        telephony=telephony,
    )
    errors.install(app)

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if 8 <= len(incoming) <= 128 else uuid.uuid4().hex
        response = await call_next(request)
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

    app.include_router(agent_api.router)
    app.include_router(connections.router)
    app.include_router(callbacks.router)
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
