"""Fake provider mode for a person trying the platform locally (docs/runbooks/local-demo.md):
one-click fake Google consent, fake Google state that survives a broker restart, Gmail
fixtures that follow the requested day, and a seeded demo contacts sheet the caller can use.
Live mode is unchanged."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caller_agent.approval import build_request
from caller_agent.config import CallerConfig
from caller_agent.results import HEADER
from caller_agent.rows import classify, read_rows
from crewquarters._transport import BrokerClient
from crewquarters.google import GoogleClients
from crewquarters.telephony import TelephonyClient
from crewquarters_broker import fakes, google
from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.models import OAuthConnection
from gmail_digest.query import build_query
from gmail_digest.window import day_window, target_date

from .conftest import Harness

START = "/internal/v1/connections/google/start"
CALLBACK = "/api/v1/connections/google/callback"
GMAIL = "/internal/v1/sdk/google/gmail/messages"
DEMO_SHEET = "demo-contacts"
FIXTURES = 8


async def _start(h: Harness, user_id: uuid.UUID, caps: list[str]) -> dict[str, str]:
    resp = await h.client.post(
        START, json={"userId": str(user_id), "capabilities": caps}, headers=h.service_headers
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, str] = resp.json()
    return body


async def _follow(h: Harness, url: str, binding: str | None) -> str:
    """The owner's browser following the authorization URL with (or without) its cookie."""
    target = httpx.URL(url)
    h.client.cookies.clear()
    if binding is not None:
        h.client.cookies.set("cq_oauth_binding", binding)
    resp = await h.client.get(target.path, params=target.params)
    h.client.cookies.clear()
    assert resp.status_code == 303
    return resp.headers["location"]


def _sdk(h: Harness, headers: dict[str, str]) -> BrokerClient:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app))
    return BrokerClient(
        "http://broker", headers["authorization"].removeprefix("Bearer "), http=http
    )


# --- 1. One-click fake consent --------------------------------------------------------------


async def test_fake_consent_url_is_the_callback_and_completes_in_one_click(
    harness: Harness, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    started = await _start(harness, user_id, ["spreadsheets", "gmail.readonly"])
    url = httpx.URL(started["authorizationUrl"])
    assert str(url).startswith(f"{harness.PUBLIC}{CALLBACK}?")
    assert url.params["code"] == "fake-code:gmail.readonly,spreadsheets"
    assert len(url.params["state"]) >= 43
    assert "accounts.google.com" not in str(url)

    location = await _follow(harness, started["authorizationUrl"], started["browserBinding"])
    assert location == f"{harness.PUBLIC}/connections/google?result=connected"
    # Still a real authorization-code exchange with the PKCE verifier.
    exchange = harness.google.token_requests[-1]
    assert exchange["grant_type"] == "authorization_code" and exchange["code_verifier"]
    async with sessions() as db:
        conn = (await db.scalars(select(OAuthConnection))).one()
        assert conn.scopes == ["gmail.readonly", "spreadsheets"] and conn.status == "CONNECTED"


async def test_fake_consent_url_carries_only_the_requested_scopes(
    harness: Harness, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    started = await _start(harness, user_id, ["gmail.readonly"])
    assert httpx.URL(started["authorizationUrl"]).params["code"] == "fake-code:gmail.readonly"
    await _follow(harness, started["authorizationUrl"], started["browserBinding"])
    async with sessions() as db:
        assert (await db.scalars(select(OAuthConnection))).one().scopes == ["gmail.readonly"]


async def test_fake_consent_still_requires_the_binding_and_a_fresh_state(
    harness: Harness, user_id: uuid.UUID
) -> None:
    """The URL alone is not enough: another browser (no cookie, or the wrong one) cannot
    finish it, and the state is single use."""
    started = await _start(harness, user_id, ["gmail.readonly"])
    url = started["authorizationUrl"]
    assert (await _follow(harness, url, None)).endswith("code=OAUTH_STATE_INVALID")
    started = await _start(harness, user_id, ["gmail.readonly"])
    url = started["authorizationUrl"]
    assert (await _follow(harness, url, "wrong")).endswith("code=OAUTH_STATE_INVALID")
    assert harness.google.token_requests == []
    # Consumed by the failed attempt: the right cookie cannot reuse it.
    assert (await _follow(harness, url, started["browserBinding"])).endswith(
        "code=OAUTH_STATE_INVALID"
    )
    started = await _start(harness, user_id, ["gmail.readonly"])
    good = await _follow(harness, started["authorizationUrl"], started["browserBinding"])
    assert good.endswith("result=connected")
    replay = await _follow(harness, started["authorizationUrl"], started["browserBinding"])
    assert replay.endswith("code=OAUTH_STATE_INVALID")


async def test_live_mode_still_sends_the_browser_to_google(
    live_harness: Harness, user_id: uuid.UUID
) -> None:
    started = await _start(live_harness, user_id, ["gmail.readonly"])
    url = httpx.URL(started["authorizationUrl"])
    assert str(url).startswith(google.AUTH_URL + "?")
    assert "code" not in url.params and url.params["code_challenge_method"] == "S256"


# --- 2. Fake Google state survives a broker restart -------------------------------------------


async def _restarted(settings: BrokerSettings) -> Harness:
    """A new broker process: a new app and new, empty fakes, on the same database."""
    return Harness(settings)


async def test_connection_survives_a_broker_restart(
    harness: Harness, broker_settings: BrokerSettings, user_id: uuid.UUID
) -> None:
    await harness.connect_google(user_id)
    restarted = await _restarted(broker_settings)
    async with restarted.app.router.lifespan_context(restarted.app):
        try:
            assert restarted.google.refresh_grants == {}  # nothing carried over in memory
            resp = await restarted.client.get(
                GMAIL, headers=restarted.agent(["google.gmail.readonly"])
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["resultSizeEstimate"] == FIXTURES
            assert [r["grant_type"] for r in restarted.google.token_requests] == ["refresh_token"]
            listing = await restarted.client.get(
                "/internal/v1/connections", headers=restarted.service_headers
            )
            row = next(c for c in listing.json() if c["provider"] == "google")
            assert row["status"] == "CONNECTED"
            assert row["grantedCapabilities"] == ["gmail.readonly", "spreadsheets"]
        finally:
            await restarted.client.aclose()


async def test_revocation_and_the_expiry_hook_still_apply_in_the_same_process(
    harness: Harness, user_id: uuid.UUID
) -> None:
    await harness.connect_google(user_id)
    [refresh] = harness.google.refresh_grants
    assert refresh.startswith("fake-refresh.")
    assert fakes.refresh_scopes(refresh) == ["gmail.readonly", "spreadsheets"]
    harness.google.revoked.update(harness.google.refresh_grants)  # the realstack "expire"
    harness.app.state.broker.google._access.clear()
    resp = await harness.client.get(GMAIL, headers=harness.agent(["google.gmail.readonly"]))
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONNECTION"


@pytest.mark.no_db
@pytest.mark.parametrize(
    "token",
    ["", "fake-refresh-0123", "fake-refresh.", "fake-refresh.!!", "other.e30", "fake-refresh.e30"],
)
def test_fake_google_rejects_malformed_refresh_tokens(token: str) -> None:
    fake = fakes.FakeGoogle()
    resp = fake.handle(
        httpx.Request(
            "POST",
            google.TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": token},
        )
    )
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_grant"


@pytest.mark.no_db
def test_fake_google_refreshes_a_token_from_another_process() -> None:
    token = fakes.refresh_token(["spreadsheets"])
    resp = fakes.FakeGoogle().handle(
        httpx.Request(
            "POST",
            google.TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": token},
        )
    )
    assert resp.status_code == 200
    assert resp.json()["scope"] == google.SCOPES["spreadsheets"]
    assert "refresh_token" not in resp.json()


# --- 3. Gmail fixtures follow the requested day -----------------------------------------------


def _list(fake: fakes.FakeGoogle, q: str) -> list[dict[str, Any]]:
    auth = {"authorization": "Bearer fake-access-x"}
    listed = fake.handle(
        httpx.Request(
            "GET", f"{google.GMAIL_URL}/messages", params={"q": q, "maxResults": 100}, headers=auth
        )
    ).json()
    return [
        fake.handle(
            httpx.Request("GET", f"{google.GMAIL_URL}/messages/{m['id']}", headers=auth)
        ).json()
        for m in listed["messages"]
    ]


@pytest.mark.no_db
@pytest.mark.parametrize("days_running", [0, 3, 40])
def test_digest_of_yesterday_finds_the_fixtures_however_long_the_broker_ran(
    monkeypatch: pytest.MonkeyPatch, days_running: int
) -> None:
    fake = fakes.FakeGoogle()  # created "at broker start"
    now = time.time() + days_running * 86_400
    monkeypatch.setattr(fakes.time, "time", lambda: now)
    tz = ZoneInfo("Asia/Kolkata")
    start, end = day_window(target_date(datetime.fromtimestamp(now, UTC), tz, None), tz)
    q = build_query(start, end, ["CATEGORY_PROMOTIONS"], [])
    messages = _list(fake, q)
    assert len(messages) == FIXTURES - 1  # the promotion is excluded
    for message in messages:
        assert start.timestamp() * 1000 <= int(message["internalDate"]) < end.timestamp() * 1000
    # Without a window, the fixtures are from the last day (not from broker start).
    unbounded = _list(fake, "")
    assert len(unbounded) == FIXTURES
    for message in unbounded:
        assert now * 1000 - 86_400_000 <= int(message["internalDate"]) < now * 1000


@pytest.mark.no_db
def test_fixtures_follow_each_requested_window() -> None:
    fake = fakes.FakeGoogle()
    for day in (datetime(2024, 2, 29, tzinfo=UTC), datetime(2031, 7, 1, tzinfo=UTC)):
        start, end = day, day + timedelta(days=1)
        messages = _list(fake, build_query(start, end, [], []))
        assert len(messages) == FIXTURES
        dates = {int(m["internalDate"]) for m in messages}
        assert min(dates) >= start.timestamp() * 1000 and max(dates) < end.timestamp() * 1000
    # `before:` is still honoured: a window too short for the fixtures is empty.
    assert _list(fake, "after:100 before:101") == []


# --- 4. Seeded demo contacts for the caller ---------------------------------------------------


async def test_seeded_demo_sheet_drives_the_caller(
    harness: Harness, user_id: uuid.UUID, real_run_id: uuid.UUID
) -> None:
    """What the caller agent does with its default ranges, through the real SDK clients:
    read the seeded contacts, plan the approval, place the calls, write results back."""
    await harness.connect_google(user_id)
    configured = await harness.client.put(
        "/internal/v1/connections/twilio",
        headers=harness.service_headers,
        json={
            "userId": str(user_id),
            "accountSid": "AC" + "0" * 31 + "7",
            "authToken": "auth-token-value-0007",
            "fromNumber": "+15555550199",
        },
    )
    assert configured.status_code == 200, configured.text
    config = CallerConfig(spreadsheet_id=DEMO_SHEET)
    headers = harness.agent(
        ["google.spreadsheets", "twilio.call.fixed_script"],
        run_id=real_run_id,
        config=config.model_dump(by_alias=True),
    )
    transport = _sdk(harness, headers)
    sheets = GoogleClients(transport).sheets

    values = await sheets.get_values(DEMO_SHEET, config.input_range)
    assert values == fakes.DEMO_CONTACTS[1:]
    plan = classify(read_rows(values, config.input_start_row), config.max_calls)
    assert [(c.row, c.name) for c in plan.eligible] == [
        (2, "Asha Rao"),
        (3, "Ben Okafor"),
        (4, "Carmen Diaz"),
    ]
    assert [(s.row, s.reason) for s in plan.skipped] == [(5, "consent"), (6, "invalid_name")]
    request = build_request(plan, config)
    assert request["title"] == "Approve 3 automated calls"

    telephony = TelephonyClient(transport)
    outcomes = []
    for contact in plan.eligible:
        call = await telephony.create_call(
            contact.phone,
            disclosure=config.disclosure,
            script=config.script.replace("{name}", contact.name),
            gather_seconds=config.response_seconds,
            idempotency_key=f"call:{real_run_id}:{contact.row}",
        )
        final = await telephony.wait_for_call(call.id, timeout_seconds=5, poll_seconds=0.05)
        outcomes.append((final.state, final.transcript))
        await sheets.update_values(
            DEMO_SHEET, f"Results!A{contact.row}:H{contact.row}", [[str(contact.row), final.state]]
        )
    assert outcomes == [
        ("completed", "Yes, I can attend."),
        ("completed", "Yes, I can attend."),
        ("no-answer", None),
    ]
    await sheets.update_values(DEMO_SHEET, "Results!A1:H1", [list(HEADER)])
    results = harness.google.sheets[DEMO_SHEET]["Results"]
    assert results[0] == list(HEADER)
    assert [r[:2] for r in results[1:4]] == [
        ["2", "completed"],
        ["3", "completed"],
        ["4", "no-answer"],
    ]
    # The contacts are untouched, and a second read does not reseed over the results.
    assert await sheets.get_values(DEMO_SHEET, config.input_range) == fakes.DEMO_CONTACTS[1:]
    assert harness.google.sheets[DEMO_SHEET]["Results"] == results


@pytest.mark.no_db
def test_only_reads_of_unknown_spreadsheets_are_seeded() -> None:
    fake = fakes.FakeGoogle()
    auth = {"authorization": "Bearer fake-access-x"}
    url = f"{google.SHEETS_URL}/written/values/Results!A1"
    fake.handle(httpx.Request("PUT", url, json={"values": [["x"]]}, headers=auth))
    assert fake.sheets == {"written": {"Results": [["x"]]}}
    read = fake.handle(
        httpx.Request("GET", f"{google.SHEETS_URL}/any-id/values/Contacts!A1:D", headers=auth)
    )
    assert read.json()["values"] == fakes.DEMO_CONTACTS
    assert set(fake.sheets) == {"written", "any-id"}
