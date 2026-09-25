"""Separate callback origins: Google redirects to the browser's origin (CQ_PUBLIC_BASE_URL);
Twilio calls back on the tunnel (CQ_TWILIO_CALLBACK_BASE_URL) and is signature-checked
against it (docs/adr/0009-callback-base-url.md, "Revision")."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.twilio import signature

from .conftest import LIVE_ALLOWED_NUMBERS, Harness

BROWSER = "https://crewquarters.local"
TUNNEL = "https://calls.example.net"
SID = "AC" + "0" * 31 + "2"
TOKEN = "auth-token-value-0002"
FROM = "+15555550199"
TO = LIVE_ALLOWED_NUMBERS[0]
DISCLOSED = "This is an automated demonstration call. Your reply will be transcribed."
CONFIG = {"script": "Hi {name}, can you attend?", "disclosure": DISCLOSED, "maxCalls": 2}


def _settings(base: BrokerSettings, **update: Any) -> BrokerSettings:
    return base.model_copy(update=update)


@pytest.mark.no_db
def test_twilio_base_falls_back_to_the_public_base(broker_settings: BrokerSettings) -> None:
    unset = _settings(broker_settings, public_base_url=BROWSER + "/")
    assert unset.twilio_base_url() == BROWSER
    # Compose passes an unset variable as an empty string.
    empty = _settings(unset, twilio_callback_base_url="")
    assert empty.twilio_base_url() == BROWSER
    split = _settings(unset, twilio_callback_base_url=TUNNEL + "/")
    assert split.twilio_base_url() == TUNNEL
    assert split.google_redirect_uri == f"{BROWSER}/api/v1/connections/google/callback"


@pytest.mark.no_db
def test_twilio_base_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CQ_PUBLIC_BASE_URL", BROWSER)
    monkeypatch.setenv("CQ_TWILIO_CALLBACK_BASE_URL", TUNNEL)
    settings = BrokerSettings()
    assert settings.twilio_base_url() == TUNNEL
    assert settings.google_redirect_uri.startswith(BROWSER + "/")


@pytest.fixture
async def split(
    broker_settings: BrokerSettings,
    sessions: async_sessionmaker[AsyncSession],
    person3_broker_tables: None,
) -> AsyncIterator[Harness]:
    settings = _settings(
        broker_settings,
        public_base_url=BROWSER,
        twilio_callback_base_url=TUNNEL,
        provider_mode="live",
        twilio_allowed_numbers=LIVE_ALLOWED_NUMBERS,
    )
    h = Harness(settings)
    async with h.app.router.lifespan_context(h.app):
        yield h
    await h.client.aclose()


async def test_google_oauth_uses_the_public_base(split: Harness, user_id: uuid.UUID) -> None:
    start = await split.client.post(
        "/internal/v1/connections/google/start",
        json={"userId": str(user_id), "capabilities": ["gmail.readonly"]},
        headers=split.service_headers,
    )
    assert start.status_code == 200, start.text
    url = httpx.URL(start.json()["authorizationUrl"])
    redirect = f"{BROWSER}/api/v1/connections/google/callback"
    assert url.params["redirect_uri"] == redirect
    split.client.cookies.set("cq_oauth_binding", start.json()["browserBinding"])
    done = await split.client.get(
        "/api/v1/connections/google/callback",
        params={"state": url.params["state"], "code": "fake-code"},
    )
    split.client.cookies.clear()
    assert done.status_code == 303
    # The owner lands back on the UI origin, not on the tunnel.
    assert done.headers["location"] == f"{BROWSER}/connections/google?result=connected"
    assert split.google.token_requests[-1]["redirect_uri"] == redirect


async def test_twilio_urls_and_signatures_use_the_twilio_base(
    split: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    configured = await split.client.put(
        "/internal/v1/connections/twilio",
        headers=split.service_headers,
        json={"userId": str(user_id), "accountSid": SID, "authToken": TOKEN, "fromNumber": FROM},
    )
    assert configured.status_code == 200, configured.text
    headers = split.agent(["twilio.call.fixed_script"], run_id=real_run_id, config=CONFIG)
    placed = await split.client.post(
        "/internal/v1/sdk/telephony/calls",
        headers=headers,
        json={
            "to": TO,
            "script": {"disclosure": DISCLOSED, "text": "Hi Asha, can you attend?"},
            "gather": {"input": "speech", "timeoutSeconds": 7},
            "idempotencyKey": "row-1",
        },
    )
    assert placed.status_code == 200, placed.text
    call_id = placed.json()["id"]
    sent = split.twilio.calls[-1]
    assert sent["Url"] == f"{TUNNEL}/api/v1/callbacks/twilio/voice/{call_id}"
    assert sent["StatusCallback"] == f"{TUNNEL}/api/v1/callbacks/twilio/status/{call_id}"

    path = f"/api/v1/callbacks/twilio/voice/{call_id}"
    params = {"CallSid": sent["sid"]}
    # Twilio signs the URL it was given: the tunnel URL. The browser origin does not verify.
    for base, status in ((BROWSER, 403), (TUNNEL, 200)):
        sig = signature(TOKEN, base + path, params)
        resp = await split.client.post(path, data=params, headers={"x-twilio-signature": sig})
        assert resp.status_code == status, (base, resp.text)
    assert f'action="{TUNNEL}/api/v1/callbacks/twilio/gather/{call_id}"' in resp.text
