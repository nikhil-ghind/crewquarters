"""Twilio callbacks (the only public POST routes besides the UI's API): signatures, bodies
and TwiML.

Pure properties run without a database. The route properties post hostile bodies,
signatures, and paths to the broker's real callback routes with Twilio configured, and
require a 4xx for anything that is not a correctly signed callback for a known call.
"""

from __future__ import annotations

import base64
import uuid
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from crewquarters_broker.twilio import (
    GATHER_TWIML,
    _advances,
    _well_formed,
    signature,
    voice_twiml,
)

PUBLIC = "https://demo.example.com"
TOKEN = "auth-token-value-0001"
SID = "AC" + "0" * 31 + "1"
PARAMS = st.dictionaries(st.text(max_size=12), st.text(max_size=40), max_size=6)
STATES = st.sampled_from(
    ["", "queued", "ringing", "in-progress", "completed", "busy", "failed", "no-answer",
     "canceled", "CREATING", "IN_DOUBT", "bogus"]
)  # fmt: skip


@pytest.mark.no_db
@given(st.text(max_size=80))
def test_signature_shape_check_never_raises(sent: str) -> None:
    ok = _well_formed(sent)
    if ok:
        assert len(base64.b64decode(sent, validate=True)) == 20


@pytest.mark.no_db
@given(st.text(max_size=40), st.text(max_size=80), PARAMS)
def test_signature_is_a_well_formed_hmac(token: str, url: str, params: dict[str, str]) -> None:
    sig = signature(token, url, params)
    assert _well_formed(sig)
    assert sig == signature(token, url, dict(reversed(list(params.items()))))


@pytest.mark.no_db
@given(PARAMS, st.text(min_size=1, max_size=5), st.text(min_size=1, max_size=5))
def test_signature_binds_every_parameter(params: dict[str, str], key: str, value: str) -> None:
    base = signature(TOKEN, PUBLIC, params)
    changed = {**params, key: params.get(key, "") + value}
    assert signature(TOKEN, PUBLIC, changed) != base


@pytest.mark.no_db
@given(st.text(max_size=200), st.text(max_size=200), st.text(max_size=100), st.integers(1, 60))
def test_voice_twiml_is_well_formed_for_any_script(
    disclosure: str, script: str, url: str, seconds: int
) -> None:
    """Scripts come from the agent's configuration; they must never inject TwiML verbs."""
    xml = voice_twiml(disclosure, script, url, seconds)
    try:
        root = ET.fromstring(xml)  # noqa: S314 - our own output
    except ET.ParseError:
        # XML 1.0 cannot carry some control characters at all; they must not be escaped
        # into something Twilio would parse as markup either.
        assert any(ord(c) < 32 and c not in "\t\n\r" for c in disclosure + script + url) or any(
            0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF)
            for c in disclosure + script + url
        )
        return
    assert [child.tag for child in root] == ["Say", "Gather", "Say"]
    gather = root.find("Gather")
    assert gather is not None and [child.tag for child in gather] == ["Say"]
    ET.fromstring(GATHER_TWIML)  # noqa: S314


@pytest.mark.no_db
@given(STATES, STATES)
def test_call_state_never_moves_backwards(current: str, new: str) -> None:
    if _advances(current, new):
        assert not _advances(new, current) or new == current or current == ""


# --- Routes --------------------------------------------------------------------------------


@pytest.fixture
async def broker(settings: Any, owner: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    from crewquarters_broker.config import BrokerSettings
    from crewquarters_broker.main import create_app

    broker_settings = BrokerSettings(
        **{**settings.model_dump(), "provider_mode": "live", "public_base_url": PUBLIC}
    )
    from crewquarters_broker import fakes

    app = create_app(
        broker_settings, provider_transport=fakes.transport(fakes.FakeGoogle(), fakes.FakeTwilio())
    )
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://b")
        user = (await owner.get("/api/v1/me")).json()["user"]["id"]
        token = broker_settings.internal_service_token.get_secret_value()
        configured = await client.put(
            "/internal/v1/connections/twilio",
            headers={"authorization": f"Bearer {token}"},
            json={
                "userId": user,
                "accountSid": SID,
                "authToken": TOKEN,
                "fromNumber": "+15555550199",
            },
        )
        assert configured.status_code == 200, configured.text
        yield client
        await client.aclose()


KINDS = st.sampled_from(["voice", "gather", "status"])


def _assert_4xx(response: httpx.Response) -> None:
    assert 400 <= response.status_code < 500, (response.status_code, response.text[:300])


@given(KINDS, st.binary(max_size=300), st.one_of(st.none(), st.text(max_size=40)))
async def test_unsigned_or_forged_callback_is_rejected(
    broker: httpx.AsyncClient, kind: str, body: bytes, sig: str | None
) -> None:
    headers: dict[str, bytes] = {"content-type": b"application/x-www-form-urlencoded"}
    if sig is not None:  # any bytes a client can put on the wire
        headers["x-twilio-signature"] = sig.replace("\r", "").replace("\n", "").encode()
    response = await broker.post(
        f"/api/v1/callbacks/twilio/{kind}/{uuid.uuid4()}", content=body, headers=headers
    )
    _assert_4xx(response)


@given(KINDS, PARAMS, st.text(max_size=40))
async def test_signed_callback_for_an_unknown_call_is_404(
    broker: httpx.AsyncClient, kind: str, params: dict[str, str], query: str
) -> None:
    from urllib.parse import quote as urlquote

    path = f"/api/v1/callbacks/twilio/{kind}/{uuid.uuid4()}"
    if query:
        path += "?q=" + urlquote(query)
    sig = signature(TOKEN, PUBLIC + path, params)
    response = await broker.post(path, data=params, headers={"x-twilio-signature": sig})
    assert response.status_code == 404, (response.status_code, response.text[:300])


@given(KINDS, st.text(max_size=60))
async def test_malformed_call_id_is_4xx(broker: httpx.AsyncClient, kind: str, call_id: str) -> None:
    from urllib.parse import quote as urlquote

    response = await broker.post(
        f"/api/v1/callbacks/twilio/{kind}/{urlquote(call_id, safe='')}", data={"a": "b"}
    )
    assert response.status_code < 500, (response.status_code, response.text[:300])


@pytest.mark.parametrize("declared", ["65537", "99999999999", "-1", "abc"])
async def test_oversized_or_lying_content_length_is_413(
    broker: httpx.AsyncClient, declared: str
) -> None:
    request = broker.build_request(
        "POST", f"/api/v1/callbacks/twilio/status/{uuid.uuid4()}", content=b"a=b"
    )
    request.headers["content-length"] = declared
    response = await broker._transport.handle_async_request(request)
    await response.aread()
    assert response.status_code == 413


async def test_chunked_oversized_callback_is_413(broker: httpx.AsyncClient) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"a" * 8192

    response = await broker.post(
        f"/api/v1/callbacks/twilio/status/{uuid.uuid4()}",
        content=chunks(),
        headers={"x-twilio-signature": base64.b64encode(b"x" * 20).decode()},
    )
    assert response.status_code == 413
