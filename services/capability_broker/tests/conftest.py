"""Fixtures for the capability broker tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker import fakes
from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.main import create_app
from crewquarters_broker.models import OAuthConnection, TelephonyCall
from crewquarters_secret_store.db import EncryptedSecret, ProviderProfile
from crewquarters_shared import capability
from crewquarters_shared.config import Settings
from crewquarters_shared.db.base import Base

PERSON3_TABLES = [
    EncryptedSecret.__table__,
    ProviderProfile.__table__,
    OAuthConnection.__table__,
    TelephonyCall.__table__,
]
PUBLIC = "https://demo.example.com"
SPREADSHEET = "sheet-0123456789abcdefghij"
KB_ID = "0190c0de-0000-7000-8000-000000000001"
LIVE_ALLOWED_NUMBERS = ["+15555550101", "+15555550102"]
# The caller's defaults: reads stay within inputRange, writes within resultRange.
DEFAULT_CONFIG = {
    "spreadsheetId": SPREADSHEET,
    "inputRange": "Contacts!A2:D",
    "resultRange": "Results!A:H",
}


@pytest.fixture(scope="session")
def person3_broker_tables(database_url: str) -> None:
    """Create Person 3 tables until their reviewed Alembic migration merges (then a no-op)."""
    engine = create_engine(database_url)
    Base.metadata.create_all(engine, tables=PERSON3_TABLES)
    engine.dispose()


@pytest.fixture
def broker_settings(settings: Settings) -> BrokerSettings:
    return BrokerSettings(
        **settings.model_dump(),
        provider_mode="fake",
        public_base_url=PUBLIC,
        google_client_id="client-id.apps.example.com",
        google_client_secret="client-secret-value",
    )


class Harness:
    """A broker wired to provider fakes, a controllable control-API run view, and
    recording knowledge and model-gateway services."""

    SDK = "/internal/v1/sdk"
    PUBLIC = PUBLIC
    SPREADSHEET = SPREADSHEET
    KB_ID = KB_ID

    def __init__(self, settings: BrokerSettings) -> None:
        self.settings = settings
        self.google = fakes.FakeGoogle(extra_messages=0)
        self.twilio = fakes.FakeTwilio()
        self.run: dict[str, Any] = {}
        self.knowledge_requests: list[dict[str, Any]] = []
        self.control_requests: list[dict[str, Any]] = []
        self.gateway_requests: list[httpx.Request] = []
        self.gateway_error: tuple[int, dict[str, Any]] | None = None
        self.app = create_app(
            settings,
            provider_transport=fakes.transport(self.google, self.twilio),
            control_transport=httpx.MockTransport(self._control),
            knowledge_transport=httpx.MockTransport(self._knowledge),
            gateway_transport=httpx.MockTransport(self._gateway),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://broker"
        )
        self.service_headers = {
            "authorization": f"Bearer {settings.internal_service_token.get_secret_value()}"
        }

    def _control(self, request: httpx.Request) -> httpx.Response:
        run_path = f"/internal/v1/runs/{self.run.get('id')}"
        if request.method == "GET" and request.url.path == run_path:
            return httpx.Response(200, json=self.run)
        if request.method == "POST" and request.url.path.startswith(run_path + "/"):
            body = json.loads(request.content)
            self.control_requests.append({"path": request.url.path, "body": body})
            if request.url.path.endswith("/events"):
                return httpx.Response(201, json={"sequence": len(self.control_requests)})
            if "/actions/" in request.url.path:
                key = request.url.path.split("/actions/")[1].split("/")[0]
                return httpx.Response(200, json={"key": key, "status": "claimed", "result": None})
            return httpx.Response(200, json={"state": "RUNNING", "cancelRequested": False})
        return httpx.Response(
            404, json={"error": {"code": "NOT_FOUND", "message": "Run not found.", "details": {}}}
        )

    def _knowledge(self, request: httpx.Request) -> httpx.Response:
        self.knowledge_requests.append(
            {"path": request.url.path, "body": json.loads(request.content)}
        )
        passage = {
            "citationId": "c1",
            "text": "Refunds are prorated.",
            "score": 0.9,
            "document": {"id": "d1", "name": "policy.md"},
            "locator": {"section": "Refunds"},
            "location": "Refunds",
        }
        return httpx.Response(200, json={"passages": [passage], "context": "..."})

    def _gateway(self, request: httpx.Request) -> httpx.Response:
        self.gateway_requests.append(request)
        if self.gateway_error is not None:
            status, error = self.gateway_error
            return httpx.Response(status, json={"error": error})
        body = json.loads(request.content)
        response = {
            "text": "Hi.",
            "structured": None,
            "finishReason": "stop",
            "usage": {"inputTokens": 3, "outputTokens": 1},
            "provider": "local",
            "model": body["profile"],
            "locality": "local",
            "latencyMs": 5,
            "requestId": "r1",
        }
        if not body.get("stream"):
            return httpx.Response(200, json=response)
        events = [
            {"type": "delta", "text": "H"},
            {"type": "delta", "text": "i."},
            {"type": "done", "response": response},
        ]
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    def agent(
        self,
        caps: list[str],
        *,
        run_id: uuid.UUID | None = None,
        permissions: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        state: str = "RUNNING",
    ) -> dict[str, str]:
        """Start a synthetic active run; return the agent's Authorization header."""
        run_id = run_id or uuid.uuid4()
        installation = uuid.uuid4()
        token, claims = capability.mint(
            signing_key=self.settings.capability_signing_key.get_secret_value(),
            run_id=run_id,
            attempt=1,
            installation_id=installation,
            agent_version_id=uuid.uuid4(),
            capabilities=caps,
            ttl_seconds=600,
        )
        self.run = {
            "id": str(run_id),
            "state": state,
            "currentAttempt": 1,
            "installationId": str(installation),
            "cancelRequested": False,
            "capabilityTokenId": claims.token_id,
            "permissions": permissions if permissions is not None else permissions_for(caps),
            "modelBindings": {},
            "config": config if config is not None else dict(DEFAULT_CONFIG),
        }
        return {"authorization": f"Bearer {token}"}

    async def connect_google(self, user_id: uuid.UUID, code: str = "fake-code") -> None:
        start = await self.client.post(
            "/internal/v1/connections/google/start",
            json={"userId": str(user_id), "capabilities": ["gmail.readonly", "spreadsheets"]},
            headers=self.service_headers,
        )
        assert start.status_code == 200, start.text
        body = start.json()
        state = httpx.URL(body["authorizationUrl"]).params["state"]
        self.client.cookies.set("cq_oauth_binding", body["browserBinding"])
        done = await self.client.get(
            "/api/v1/connections/google/callback", params={"state": state, "code": code}
        )
        self.client.cookies.clear()
        assert done.headers["location"].endswith("result=connected"), done.headers["location"]


def permissions_for(caps: list[str]) -> dict[str, Any]:
    """The approved-permissions block that yields these capability strings."""
    return {
        "llmProfiles": [
            c.removeprefix("llm.profile:") for c in caps if c.startswith("llm.profile:")
        ],
        "knowledge": ["config"] if "knowledge.search:config" in caps else [],
        "connectors": {
            "google": [c.removeprefix("google.") for c in caps if c.startswith("google.")],
            "twilio": [c.removeprefix("twilio.") for c in caps if c.startswith("twilio.")],
        },
        "cloudProviders": [c.removeprefix("cloud.") for c in caps if c.startswith("cloud.")],
        "userInput": "user_input" in caps,
    }


@pytest.fixture
async def harness(
    broker_settings: BrokerSettings,
    sessions: async_sessionmaker[AsyncSession],
    person3_broker_tables: None,
) -> AsyncIterator[Harness]:
    h = Harness(broker_settings)
    async with h.app.router.lifespan_context(h.app):
        yield h
    await h.client.aclose()


@pytest.fixture
async def live_harness(
    broker_settings: BrokerSettings,
    sessions: async_sessionmaker[AsyncSession],
    person3_broker_tables: None,
) -> AsyncIterator[Harness]:
    """Live provider mode (no simulated callbacks), still served by the fake transport."""
    settings = broker_settings.model_copy(
        update={"provider_mode": "live", "twilio_allowed_numbers": LIVE_ALLOWED_NUMBERS}
    )
    h = Harness(settings)
    async with h.app.router.lifespan_context(h.app):
        yield h
    await h.client.aclose()


@pytest.fixture
async def user_id(owner: httpx.AsyncClient, person3_broker_tables: None) -> uuid.UUID:
    me = await owner.get("/api/v1/me")
    return uuid.UUID(me.json()["user"]["id"])


@pytest.fixture
async def real_run_id(owner: httpx.AsyncClient, catalog_synced: None) -> uuid.UUID:
    """A QUEUED run row (for foreign keys); tests give it a synthetic active run view."""
    install = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": {},
            "approvedPermissions": {
                "llmProfiles": ["local.general"],
                "knowledge": [],
                "connectors": {},
                "cloudProviders": [],
                "userInput": True,
            },
        },
    )
    assert install.status_code == 201, install.text
    run = await owner.post("/api/v1/runs", json={"installationId": install.json()["id"]})
    assert run.status_code in (200, 201, 202), run.text
    return uuid.UUID(run.json()["id"])
