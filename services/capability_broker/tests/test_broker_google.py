"""Google OAuth (CSRF, replay, binding, incremental scopes, expiry) and Gmail/Sheets."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker import fakes, google
from crewquarters_broker.models import OAuthConnection
from crewquarters_secret_store import Keyring
from crewquarters_secret_store.db import EncryptedSecret
from crewquarters_shared.db.models import AuditEvent

START = "/internal/v1/connections/google/start"
CALLBACK = "/api/v1/connections/google/callback"
SDK = "/internal/v1/sdk"
GMAIL = f"{SDK}/google/gmail/messages"


async def _start(h: Any, user_id: uuid.UUID, caps: list[str]) -> tuple[str, str, httpx.URL]:
    resp = await h.client.post(
        START, json={"userId": str(user_id), "capabilities": caps}, headers=h.service_headers
    )
    assert resp.status_code == 200, resp.text
    url = httpx.URL(resp.json()["authorizationUrl"])
    return url.params["state"], resp.json()["browserBinding"], url


async def _callback(h: Any, binding: str | None, **params: str) -> str:
    h.client.cookies.clear()
    if binding:
        h.client.cookies.set("cq_oauth_binding", binding)
    resp = await h.client.get(CALLBACK, params=params)
    h.client.cookies.clear()
    assert resp.status_code == 303
    return resp.headers["location"]


async def test_start_builds_exact_consent_url(live_harness: Any, user_id: uuid.UUID) -> None:
    """Live mode; fake mode sends the browser to the callback (test_broker_fake_demo.py)."""
    state, _, url = await _start(live_harness, user_id, ["spreadsheets", "gmail.readonly"])
    params = url.params
    assert str(url).startswith(google.AUTH_URL + "?")
    assert params["redirect_uri"] == f"{live_harness.PUBLIC}/api/v1/connections/google/callback"
    requested = [google.SCOPES["gmail.readonly"], google.SCOPES["spreadsheets"]]
    assert params["scope"] == " ".join(sorted(requested))
    assert params["access_type"] == "offline" and params["include_granted_scopes"] == "true"
    assert params["code_challenge_method"] == "S256" and len(state) >= 43


async def test_start_requires_service_token_and_valid_scopes(
    harness: Any, user_id: uuid.UUID
) -> None:
    body = {"userId": str(user_id), "capabilities": ["gmail.readonly"]}
    assert (await harness.client.post(START, json=body)).status_code == 401
    bad = {"userId": str(user_id), "capabilities": ["gmail.modify"]}
    resp = await harness.client.post(START, json=bad, headers=harness.service_headers)
    assert resp.status_code == 422


async def test_callback_stores_encrypted_refresh_token(
    live_harness: Any, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # Live mode, whose consent URL carries the PKCE challenge the exchange must match.
    harness = live_harness
    state, binding, url = await _start(harness, user_id, ["gmail.readonly", "spreadsheets"])
    location = await _callback(harness, binding, state=state, code="fake-code")
    assert location == f"{harness.PUBLIC}/connections/google?result=connected"
    # PKCE: the verifier sent at exchange time matches the challenge in the consent URL.
    exchange = harness.google.token_requests[-1]
    digest = hashlib.sha256(exchange["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == url.params["code_challenge"]
    assert exchange["redirect_uri"] == f"{harness.PUBLIC}/api/v1/connections/google/callback"

    async with sessions() as db:
        conn = (await db.scalars(select(OAuthConnection))).one()
        assert conn.scopes == ["gmail.readonly", "spreadsheets"]
        assert conn.provider_subject == "owner@example.com"
        secret = await db.get(EncryptedSecret, conn.encrypted_secret_id)
        assert secret is not None and b"fake-refresh" not in secret.ciphertext
        dump = (await db.execute(text("SELECT * FROM oauth_connections"))).all()
        assert "fake-refresh" not in str(dump)
        actions = (await db.scalars(select(AuditEvent.action))).all()
        assert "connection.google.connected" in actions

    resp = await harness.client.get("/internal/v1/connections", headers=harness.service_headers)
    google_row = next(c for c in resp.json() if c["provider"] == "google")
    assert google_row["status"] == "CONNECTED"
    assert google_row["grantedCapabilities"] == ["gmail.readonly", "spreadsheets"]
    assert "fake-refresh" not in resp.text and "fake-access" not in resp.text


async def test_state_is_single_use(harness: Any, user_id: uuid.UUID) -> None:
    state, binding, _ = await _start(harness, user_id, ["gmail.readonly"])
    assert (await _callback(harness, binding, state=state, code="fake-code")).endswith("connected")
    replay = await _callback(harness, binding, state=state, code="fake-code")
    assert replay.endswith("result=error&code=OAUTH_STATE_INVALID")


async def test_unknown_or_expired_state(harness: Any, user_id: uuid.UUID) -> None:
    location = await _callback(harness, "x", state="forged", code="fake-code")
    assert location.endswith("code=OAUTH_STATE_INVALID")
    state, binding, _ = await _start(harness, user_id, ["gmail.readonly"])
    connector = harness.app.state.broker.google
    key = hashlib.sha256(state.encode()).hexdigest()
    pending = connector._pending[key]
    connector._pending[key] = type(pending)(
        pending.user_id, pending.code_verifier, pending.binding_hash, time.monotonic() - 1
    )
    assert (await _callback(harness, binding, state=state, code="fake-code")).endswith(
        "code=OAUTH_STATE_INVALID"
    )


async def test_browser_binding_required(harness: Any, user_id: uuid.UUID) -> None:
    """Login CSRF: a victim's browser without the binding cookie cannot finish the flow."""
    state, _, _ = await _start(harness, user_id, ["gmail.readonly"])
    assert (await _callback(harness, None, state=state, code="fake-code")).endswith(
        "code=OAUTH_STATE_INVALID"
    )
    state, _, _ = await _start(harness, user_id, ["gmail.readonly"])
    assert (await _callback(harness, "wrong", state=state, code="fake-code")).endswith(
        "code=OAUTH_STATE_INVALID"
    )
    assert harness.google.token_requests == []


async def test_denied_consent_and_bad_code(harness: Any, user_id: uuid.UUID) -> None:
    state, binding, _ = await _start(harness, user_id, ["gmail.readonly"])
    assert (await _callback(harness, binding, state=state, error="access_denied")).endswith(
        "code=OAUTH_DENIED"
    )
    state, binding, _ = await _start(harness, user_id, ["gmail.readonly"])
    assert (await _callback(harness, binding, state=state, code="stolen")).endswith(
        "code=OAUTH_CODE_INVALID"
    )


async def test_partial_grant_and_reconnect_replaces(
    harness: Any, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    state, binding, _ = await _start(harness, user_id, ["gmail.readonly", "spreadsheets"])
    await _callback(harness, binding, state=state, code="fake-code:gmail.readonly")
    headers = harness.agent(["google.spreadsheets"])
    resp = await harness.client.post(
        f"{SDK}/google/sheets/values:get", headers=headers, json=_range(harness, "Contacts!A2:D")
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONNECTION"

    await harness.connect_google(user_id)  # incremental consent grants both
    async with sessions() as db:
        conns = (await db.scalars(select(OAuthConnection))).all()
        assert len(conns) == 1 and conns[0].scopes == ["gmail.readonly", "spreadsheets"]
        assert len((await db.scalars(select(EncryptedSecret))).all()) == 1


async def test_fake_google_accumulates_separate_consents_until_revoked(
    harness: Any, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The broker asks for include_granted_scopes=true, so consenting to Sheets after Gmail
    keeps Gmail (as with Google). Found by the real-stack suite: the fake used to grant only
    the latest consent's scopes, so connecting Sheets silently dropped Gmail."""
    for scope in ("gmail.readonly", "spreadsheets"):
        state, binding, _ = await _start(harness, user_id, [scope])
        location = await _callback(harness, binding, state=state, code=f"fake-code:{scope}")
        assert location.endswith("result=connected")
    async with sessions() as db:
        conns = (await db.scalars(select(OAuthConnection))).all()
        assert len(conns) == 1 and conns[0].scopes == ["gmail.readonly", "spreadsheets"]

    resp = await harness.client.delete(
        f"/internal/v1/connections/google?userId={user_id}", headers=harness.service_headers
    )
    assert resp.status_code in (200, 204), resp.text
    state, binding, _ = await _start(harness, user_id, ["spreadsheets"])
    await _callback(harness, binding, state=state, code="fake-code:spreadsheets")
    async with sessions() as db:
        conns = (await db.scalars(select(OAuthConnection))).all()
        assert [c.scopes for c in conns] == [["spreadsheets"]]  # revoking forgot the grant


async def test_expired_refresh_needs_reconnect(
    harness: Any, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Google test mode expires refresh tokens after seven days."""
    await harness.connect_google(user_id)
    harness.google.revoked.update(harness.google.refresh_grants)
    harness.app.state.broker.google._access.clear()
    headers = harness.agent(["google.gmail.readonly"])
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NEEDS_CONNECTION"
    listing = await harness.client.get("/internal/v1/connections", headers=harness.service_headers)
    google_row = next(c for c in listing.json() if c["provider"] == "google")
    assert google_row["status"] == "NEEDS_ATTENTION" and google_row["grantedCapabilities"] == []
    async with sessions() as db:
        assert "connection.google.expired" in (await db.scalars(select(AuditEvent.action))).all()


async def test_access_token_refresh_and_401_retry(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    connector = harness.app.state.broker.google
    [conn_id] = connector._access
    harness.google.token_requests.clear()
    # Google rejects the cached token with 401; the broker refreshes once and retries.
    connector._access[conn_id] = ("stale", time.monotonic() + 3600)
    headers = harness.agent(["google.gmail.readonly"])
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.status_code == 200, resp.text
    assert [r["grant_type"] for r in harness.google.token_requests] == ["refresh_token"]


async def test_test_and_disconnect_revokes(
    harness: Any, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await harness.connect_google(user_id)
    tested = await harness.client.post(
        "/internal/v1/connections/google/test", headers=harness.service_headers
    )
    assert tested.status_code == 200 and tested.json()["status"] == "CONNECTED"
    refresh = next(iter(harness.google.refresh_grants))
    resp = await harness.client.delete(
        "/internal/v1/connections/google",
        params={"userId": str(user_id)},
        headers=harness.service_headers,
    )
    assert resp.status_code == 204
    assert refresh in harness.google.revoked
    async with sessions() as db:
        assert (await db.scalars(select(OAuthConnection))).all() == []
        assert (await db.scalars(select(EncryptedSecret))).all() == []
    headers = harness.agent(["google.gmail.readonly"])
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.json()["error"]["code"] == "NEEDS_CONNECTION"


async def test_concurrent_refreshes_are_single_flight_and_hold_no_connection(
    harness: Any, user_id: uuid.UUID
) -> None:
    await harness.connect_google(user_id)
    state = harness.app.state.broker
    connector = state.google
    pool = state.sessions.kw["bind"].pool
    connector._access.clear()
    harness.google.token_requests.clear()
    refreshing, release = asyncio.Event(), asyncio.Event()

    async def slow_token_endpoint(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/token":
            refreshing.set()
            await release.wait()
        return harness.google.handle(request)

    real_http = connector.http
    connector.http = httpx.AsyncClient(transport=httpx.MockTransport(slow_token_endpoint))
    try:
        headers = harness.agent(["google.gmail.readonly"])
        calls = [asyncio.create_task(harness.client.get(GMAIL, headers=headers)) for _ in range(5)]
        await asyncio.wait_for(refreshing.wait(), 5)
        await asyncio.sleep(0.1)  # the other four queue up behind the refresh
        held_while_waiting = pool.checkedout()
        release.set()
        responses = await asyncio.gather(*calls)
    finally:
        await connector.http.aclose()
        connector.http = real_http
    assert [r.status_code for r in responses] == [200] * 5
    assert [r["grant_type"] for r in harness.google.token_requests] == ["refresh_token"]
    assert held_while_waiting == 0  # neither the refresh nor the four waiting hold one


async def test_disconnect_works_when_the_secret_cannot_be_decrypted(
    harness: Any, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """E.g. the master key version that encrypted it is no longer in the keyring."""
    await harness.connect_google(user_id)
    connector = harness.app.state.broker.google
    connector.keyring = Keyring({2: bytes(range(32))})
    connector._access.clear()
    headers = harness.agent(["google.gmail.readonly"])
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONNECTION"
    resp = await harness.client.delete(
        "/internal/v1/connections/google",
        params={"userId": str(user_id)},
        headers=harness.service_headers,
    )
    assert resp.status_code == 204, resp.text
    async with sessions() as db:
        assert (await db.scalars(select(OAuthConnection))).all() == []
        assert (await db.scalars(select(EncryptedSecret))).all() == []


def _google_answers(h: Any, path: str, response: httpx.Response) -> None:
    handle = h.google.handle

    def patched(request: httpx.Request) -> httpx.Response:
        return response if request.url.path.endswith(path) else handle(request)

    h.google.handle = patched


async def test_callback_with_unusable_tokens_redirects_with_an_error(
    harness: Any, user_id: uuid.UUID
) -> None:
    _google_answers(harness, "/gmail/v1/users/me/profile", httpx.Response(401))
    state, binding, _ = await _start(harness, user_id, ["gmail.readonly"])
    location = await _callback(harness, binding, state=state, code="fake-code")
    assert location.endswith("result=error&code=OAUTH_TOKEN_INVALID"), location

    harness.google.handle = fakes.FakeGoogle(extra_messages=0).handle
    no_access = {"refresh_token": "r", "scope": google.SCOPES["gmail.readonly"]}
    _google_answers(harness, "/token", httpx.Response(200, json=no_access))
    state, binding, _ = await _start(harness, user_id, ["gmail.readonly"])
    location = await _callback(harness, binding, state=state, code="fake-code")
    assert location.endswith("result=error&code=OAUTH_TOKEN_INVALID"), location


async def test_second_401_is_a_provider_error(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    _google_answers(harness, "/gmail/v1/users/me/messages", httpx.Response(401))
    resp = await harness.client.get(GMAIL, headers=harness.agent(["google.gmail.readonly"]))
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "PROVIDER_ERROR"


async def test_gmail_list_paginates_and_get_passes_through(
    harness: Any, user_id: uuid.UUID
) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(["google.gmail.readonly"])
    ids: list[str] = []
    page: str | None = None
    while True:
        params = {"maxResults": "3", **({"pageToken": page} if page else {})}
        resp = await harness.client.get(GMAIL, params=params, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["resultSizeEstimate"] == 8
        ids += [m["id"] for m in resp.json()["messages"]]
        page = resp.json().get("nextPageToken")
        if not page:
            break
    assert len(ids) == len(set(ids)) == 8

    promos = await harness.client.get(
        GMAIL, params={"labelIds": ["INBOX", "CATEGORY_PROMOTIONS"]}, headers=headers
    )
    assert [m["id"] for m in promos.json()["messages"]] == ["m-promo"]
    # Search operators the digest agent sends (found by the real-stack suite: the fake
    # ignored them, so promotions reached the digest).
    no_promos = await harness.client.get(
        GMAIL, params={"q": "-category:promotions"}, headers=headers
    )
    assert "m-promo" not in {m["id"] for m in no_promos.json()["messages"]}
    assert no_promos.json()["resultSizeEstimate"] == 7
    labelled = await harness.client.get(
        GMAIL, params={"q": "label:CATEGORY_PROMOTIONS"}, headers=headers
    )
    assert [m["id"] for m in labelled.json()["messages"]] == ["m-promo"]

    resp = await harness.client.get(f"{GMAIL}/m-html", headers=headers)
    assert resp.json() == harness.google.messages["m-html"]  # format=full, unmodified
    missing = await harness.client.get(f"{GMAIL}/nope", headers=headers)
    assert missing.status_code == 404
    bad = await harness.client.get(f"{GMAIL}/..%2Fx", headers=headers)
    assert bad.status_code in (404, 422)


def _range(h: Any, cells: str, values: list[list[Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"spreadsheetId": h.SPREADSHEET, "range": cells}
    if values is not None:
        body["values"] = values
    return body


async def test_sheets_use_configured_spreadsheet_only(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(["google.spreadsheets"])
    formula = [['=IMPORTXML("http://x")', 1, True, None]]
    append = await harness.client.post(
        f"{SDK}/google/sheets/values:append",
        headers=headers,
        json=_range(harness, "Results!A:H", formula),
    )
    assert append.status_code == 200, append.text
    assert append.json()["updatedRows"] == 1
    rows = harness.google.sheets[harness.SPREADSHEET]["Results"]
    assert rows == formula
    update = await harness.client.post(
        f"{SDK}/google/sheets/values:update",
        headers=headers,
        json=_range(harness, "Results!A1", [["done"]]),
    )
    assert update.status_code == 200 and rows[0] == ["done"]
    harness.google.sheets[harness.SPREADSHEET]["Contacts"] = [["name"], ["Asha"]]
    read = await harness.client.post(
        f"{SDK}/google/sheets/values:get", headers=headers, json=_range(harness, "Contacts!A2:D")
    )
    assert read.json()["values"] == [["Asha"]]

    other = await harness.client.post(
        f"{SDK}/google/sheets/values:append",
        headers=headers,
        json={"spreadsheetId": "someone-elses-sheet", "range": "A:A", "values": [["x"]]},
    )
    assert other.status_code == 403 and other.json()["error"]["code"] == "PERMISSION_DENIED"
    assert set(harness.google.sheets) == {harness.SPREADSHEET}


@pytest.mark.parametrize(
    ("operation", "cells", "allowed"),
    [
        ("get", "Contacts!A2:D", True),
        ("get", "Contacts!B5:C9", True),
        ("get", "contacts!a2:d2", True),  # A1 tab names and columns ignore case
        ("get", "Contacts!A1:D", False),  # the header row is outside inputRange
        ("get", "Contacts!A2:E", False),
        ("get", "Contacts", False),
        ("get", "Results!A2:D", False),  # reads never leave inputRange
        ("get", "'Other tab'!A2:D", False),
        ("update", "Results!A1:H1", True),
        ("update", "Results!A7:H7", True),
        ("update", "Results!H3", True),
        ("update", "Results!A1:I1", False),
        ("update", "Contacts!A2:D2", False),  # writes never touch the contacts
        ("update", "Results", False),
        ("append", "Results!A:H", True),
        ("append", "Results!A:Z", False),
        ("append", "Contacts!A:D", False),
    ],
)
async def test_sheets_are_scoped_to_the_configured_ranges(
    harness: Any, user_id: uuid.UUID, operation: str, cells: str, allowed: bool
) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(["google.spreadsheets"])
    values = None if operation == "get" else [["x"]]
    resp = await harness.client.post(
        f"{SDK}/google/sheets/values:{operation}",
        headers=headers,
        json=_range(harness, cells, values),
    )
    if allowed:
        assert resp.status_code == 200, resp.text
    else:
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "PERMISSION_DENIED"
        assert resp.json()["error"]["details"]["key"] in ("inputRange", "resultRange")


@pytest.mark.parametrize("cells", ["A1", "A2:D", "Contacts!A2:D!B", "Contacts!2A", "'x!A1"])
async def test_sheets_ranges_must_be_a1_with_a_tab(
    harness: Any, user_id: uuid.UUID, cells: str
) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(["google.spreadsheets"])
    resp = await harness.client.post(
        f"{SDK}/google/sheets/values:get", headers=headers, json=_range(harness, cells)
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_REQUEST"


async def test_sheets_need_configured_ranges(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(["google.spreadsheets"], config={"spreadsheetId": harness.SPREADSHEET})
    for operation, key in (("get", "inputRange"), ("append", "resultRange")):
        resp = await harness.client.post(
            f"{SDK}/google/sheets/values:{operation}",
            headers=headers,
            json=_range(harness, "Results!A1", None if operation == "get" else [["x"]]),
        )
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"
        assert resp.json()["error"]["details"] == {"key": key}


async def test_sheets_input_is_raw(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    seen: list[httpx.Request] = []
    handle = harness.google.handle

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    harness.google.handle = spy
    headers = harness.agent(["google.spreadsheets"])
    await harness.client.post(
        f"{SDK}/google/sheets/values:append",
        headers=headers,
        json=_range(harness, "Results!A:A", [["=1+1"]]),
    )
    assert seen[-1].url.params["valueInputOption"] == "RAW"


async def test_sheets_need_configured_spreadsheet(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(["google.spreadsheets"], config={})
    resp = await harness.client.post(
        f"{SDK}/google/sheets/values:get", headers=headers, json=_range(harness, "A1")
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"


async def test_no_secret_reaches_the_logs(
    harness: Any, user_id: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await harness.connect_google(user_id)
        harness.app.state.broker.google._access.clear()  # force a refresh
        headers = harness.agent(["google.gmail.readonly"])
        await harness.client.get(f"{GMAIL}/m-plain", headers=headers)
        await harness.client.delete(
            "/internal/v1/connections/google",
            params={"userId": str(user_id)},
            headers=harness.service_headers,
        )
    logged = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    for secret in (
        "fake-refresh-",
        "fake-access-",
        "client-secret-value",
        headers["authorization"],
    ):
        assert secret not in logged
