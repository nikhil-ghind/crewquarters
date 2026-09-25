"""Broker test fixtures and helpers. Import into a test module; not a conftest, because
every ``conftest.py`` shares one module name and would shadow the root conftest."""

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
    """A broker wired to provider fakes, a controllable control-API run view, and a
    recording knowledge service."""

    PUBLIC = PUBLIC
    SPREADSHEET = SPREADSHEET
    KB_ID = KB_ID

    def __init__(self, settings: BrokerSettings) -> None:
        self.settings = settings
        self.google = fakes.FakeGoogle(extra_messages=0)
        self.twilio = fakes.FakeTwilio()
        self.run: dict[str, Any] = {}
        self.knowledge_requests: list[dict[str, Any]] = []
        self.app = create_app(
            settings,
            provider_transport=fakes.transport(self.google, self.twilio),
            control_transport=httpx.MockTransport(self._control),
            knowledge_transport=httpx.MockTransport(self._knowledge),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://broker"
        )
        self.service_headers = {
            "authorization": f"Bearer {settings.internal_service_token.get_secret_value()}"
        }

    def _control(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/internal/v1/runs/{self.run.get('id')}":
            return httpx.Response(200, json=self.run)
        return httpx.Response(
            404, json={"error": {"code": "NOT_FOUND", "message": "Run not found.", "details": {}}}
        )

    def _knowledge(self, request: httpx.Request) -> httpx.Response:
        self.knowledge_requests.append(
            {"path": request.url.path, "body": json.loads(request.content)}
        )
        return httpx.Response(200, json={"passages": []})

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
            "config": config if config is not None else {"spreadsheetId": SPREADSHEET},
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
        "llmProfiles": [],
        "knowledge": ["config"] if "knowledge.search:config" in caps else [],
        "connectors": {
            "google": [c.removeprefix("google.") for c in caps if c.startswith("google.")],
            "twilio": [c.removeprefix("twilio.") for c in caps if c.startswith("twilio.")],
        },
        "cloudProviders": [],
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
