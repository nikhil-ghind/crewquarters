"""Twilio: credentials, idempotent calls, signed callbacks, dedupe, and redaction."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker.twilio import DISCLOSURE, signature
from crewquarters_secret_store.db import EncryptedSecret
from crewquarters_shared.db.models import AuditEvent

if TYPE_CHECKING:
    from broker_testkit import Harness

pytest_plugins = ["broker_testkit"]

SID = "AC" + "0" * 31 + "1"
TOKEN = "auth-token-value-0001"
FROM = "+15555550199"
TO = "+15555550101"
CALLS = "/agent/v1/telephony/calls"
CONFIG = {"script": "Hi {name}, can you attend on Friday?", "maxCalls": 2, "responseSeconds": 7}


async def _configure(h: Harness, user_id: uuid.UUID, token: str = TOKEN) -> httpx.Response:
    return await h.client.put(
        "/internal/v1/connections/twilio",
        headers=h.service_headers,
        json={"userId": str(user_id), "accountSid": SID, "authToken": token, "fromNumber": FROM},
    )


def _agent(h: Harness, run_id: uuid.UUID, **config: Any) -> dict[str, str]:
    return h.agent(["twilio.call.fixed_script"], run_id=run_id, config={**CONFIG, **config})


async def _call(
    h: Harness, headers: dict[str, str], key: str = "row-1", to: str = TO, name: str = "Asha"
) -> httpx.Response:
    return await h.client.post(
        CALLS, headers=headers, json={"to": to, "idempotencyKey": key, "variables": {"name": name}}
    )


async def _signed(
    h: Harness, path: str, params: dict[str, str], token: str = TOKEN
) -> httpx.Response:
    sig = signature(token, h.PUBLIC + path, params)
    return await h.client.post(path, data=params, headers={"x-twilio-signature": sig})


@pytest.mark.no_db
def test_signature_matches_twilio_reference_vector() -> None:
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    assert signature("12345", url, params) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


async def test_configure_validates_and_never_returns_secret(
    harness: Harness, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    resp = await _configure(harness, user_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CONNECTED"
    assert resp.json()["fromNumber"] == "***0199"
    listing = await harness.client.get("/internal/v1/connections", headers=harness.service_headers)
    assert TOKEN not in listing.text and FROM not in listing.text
    twilio = next(c for c in listing.json() if c["provider"] == "twilio")
    assert twilio["grantedCapabilities"] == ["call.fixed_script"]

    replaced = await _configure(harness, user_id, token="invalid-token-00000")
    assert replaced.json()["status"] == "NEEDS_ATTENTION"
    async with sessions() as db:
        [secret] = (await db.scalars(select(EncryptedSecret))).all()
        assert TOKEN.encode() not in secret.ciphertext

    for bad in ({"accountSid": "nope"}, {"fromNumber": "5550199"}, {"authToken": "short"}):
        body = {
            "userId": str(user_id),
            "accountSid": SID,
            "authToken": TOKEN,
            "fromNumber": FROM,
            **bad,
        }
        resp = await harness.client.put(
            "/internal/v1/connections/twilio", json=body, headers=harness.service_headers
        )
        assert resp.status_code == 422, bad

    resp = await harness.client.delete(
        "/internal/v1/connections/twilio",
        params={"userId": str(user_id)},
        headers=harness.service_headers,
    )
    assert resp.status_code == 204
    async with sessions() as db:
        assert (await db.scalars(select(EncryptedSecret))).all() == []


async def test_call_needs_connection_and_valid_number(
    harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    headers = _agent(harness, real_run_id)
    resp = await _call(harness, headers)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONNECTION"
    await _configure(harness, user_id)
    for bad in ("5555550101", "+0123456789", "+1555", "+1 555 555 0101"):
        assert (await _call(harness, headers, to=bad)).status_code == 422, bad
    assert harness.twilio.calls == []


async def test_duplicate_start_places_one_call(
    harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await _configure(harness, user_id)
    headers = _agent(harness, real_run_id)
    first, second = await asyncio.gather(_call(harness, headers), _call(harness, headers))
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["id"] == second.json()["id"]
    assert len(harness.twilio.calls) == 1
    placed = harness.twilio.calls[0]
    assert placed["To"] == TO and placed["From"] == FROM
    assert placed["Url"] == f"{harness.PUBLIC}/api/v1/callbacks/twilio/voice/{first.json()['id']}"


async def test_call_cap_per_run(
    harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await _configure(harness, user_id)
    headers = _agent(harness, real_run_id)
    assert (await _call(harness, headers, key="a")).status_code == 200
    assert (await _call(harness, headers, key="b")).status_code == 200
    third = await _call(harness, headers, key="c")
    assert third.status_code == 409 and third.json()["error"]["code"] == "CALL_LIMIT_REACHED"
    assert (await _call(harness, headers, key="a")).status_code == 200  # replay still works


async def test_cancel_stops_new_calls(
    harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await _configure(harness, user_id)
    headers = _agent(harness, real_run_id)
    harness.run["cancelRequested"] = True
    assert (await _call(harness, headers)).json()["error"]["code"] == "CANCELLED"
    assert harness.twilio.calls == []


@pytest.mark.parametrize(
    ("to", "outcome", "transcript"),
    [
        ("+15555550101", "answered_speech", "Yes, I can attend."),
        ("+15555550102", "busy", None),
        ("+15555550103", "no_answer", None),
        ("+15555550104", "failed", None),
        ("+15555550105", "answered_no_speech", None),
    ],
)
async def test_fake_call_outcomes(
    harness: Harness,
    real_run_id: uuid.UUID,
    user_id: uuid.UUID,
    to: str,
    outcome: str,
    transcript: str | None,
) -> None:
    await _configure(harness, user_id)
    headers = _agent(harness, real_run_id)
    call_id = (await _call(harness, headers, to=to)).json()["id"]
    for _ in range(100):
        result = (await harness.client.get(f"{CALLS}/{call_id}", headers=headers)).json()
        if result["outcome"] != "pending":
            break
        await asyncio.sleep(0.02)
    assert result["outcome"] == outcome
    assert result["transcript"] == transcript
    assert result["to"] == f"***{to[-4:]}"


async def test_live_mode_only_calls_allowed_numbers(
    live_harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await _configure(live_harness, user_id)
    headers = _agent(live_harness, real_run_id)
    resp = await _call(live_harness, headers, to="+15555550177")
    assert resp.status_code == 403
    assert "+15555550177" not in resp.text
    assert live_harness.twilio.calls == []


async def test_lost_response_is_in_doubt_and_never_redialed(
    live_harness: Harness,
    real_run_id: uuid.UUID,
    user_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _configure(live_harness, user_id)
    headers = _agent(live_harness, real_run_id)
    live_harness.twilio.fail_next = "timeout"
    with caplog.at_level(logging.DEBUG):
        resp = await _call(live_harness, headers)
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "PROVIDER_IN_DOUBT"
    retry = await _call(live_harness, headers)
    assert retry.status_code == 200 and retry.json()["outcome"] == "in_doubt"
    assert live_harness.twilio.calls == []  # the timed-out request is the only attempt
    assert TO not in caplog.text

    live_harness.twilio.fail_next = "error"
    assert (await _call(live_harness, headers, key="row-2", to="+15555550102")).status_code == 502
    assert (await _call(live_harness, headers, key="row-2", to="+15555550102")).json()[
        "outcome"
    ] == ("in_doubt")


async def test_rejected_call_is_failed(
    live_harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await _configure(live_harness, user_id, token="invalid-token-00000")
    headers = _agent(live_harness, real_run_id)
    resp = await _call(live_harness, headers)
    assert resp.status_code == 200 and resp.json()["outcome"] == "failed"


async def _placed(
    live_harness: Harness, run_id: uuid.UUID, user_id: uuid.UUID, name: str = "Asha"
) -> tuple[str, str, dict[str, str]]:
    await _configure(live_harness, user_id)
    headers = _agent(live_harness, run_id)
    call_id = (await _call(live_harness, headers, name=name)).json()["id"]
    return call_id, live_harness.twilio.calls[-1]["sid"], headers


async def test_voice_twiml_is_fixed_and_escaped(
    live_harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    call_id, sid, _ = await _placed(live_harness, real_run_id, user_id, name="<Play>{x}</Play>&")
    resp = await _signed(
        live_harness, f"/api/v1/callbacks/twilio/voice/{call_id}", {"CallSid": sid}
    )
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/xml"
    xml = resp.text
    assert xml.index(DISCLOSURE) < xml.index("<Gather")
    assert "<Play>" not in xml and "Hi Playx/Play&amp;, can you attend" in xml
    assert 'timeout="7"' in xml and 'input="speech"' in xml
    assert f'action="{live_harness.PUBLIC}/api/v1/callbacks/twilio/gather/{call_id}"' in xml


async def test_callback_signature_required(
    live_harness: Harness,
    real_run_id: uuid.UUID,
    user_id: uuid.UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    call_id, sid, _ = await _placed(live_harness, real_run_id, user_id)
    path = f"/api/v1/callbacks/twilio/status/{call_id}"
    params = {"CallSid": sid, "CallStatus": "completed"}
    assert (await live_harness.client.post(path, data=params)).status_code == 403
    forged = await _signed(live_harness, path, params, token="attacker-token-000000")
    assert forged.status_code == 403
    # A signature for another URL (e.g. the internal host) is not accepted either.
    sig = signature(TOKEN, "http://broker" + path, params)
    resp = await live_harness.client.post(path, data=params, headers={"x-twilio-signature": sig})
    assert resp.status_code == 403
    async with sessions() as db:
        rejected = (
            await db.scalars(
                select(AuditEvent).where(AuditEvent.action == "callback.twilio.rejected")
            )
        ).all()
        assert len(rejected) == 3


async def test_callback_for_another_call_sid(
    live_harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    call_id, _, _ = await _placed(live_harness, real_run_id, user_id)
    resp = await _signed(
        live_harness, f"/api/v1/callbacks/twilio/voice/{call_id}", {"CallSid": "CAother"}
    )
    assert resp.status_code == 404


async def test_duplicate_and_late_callbacks_are_harmless(
    live_harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    call_id, sid, headers = await _placed(live_harness, real_run_id, user_id)
    gather = f"/api/v1/callbacks/twilio/gather/{call_id}"
    status = f"/api/v1/callbacks/twilio/status/{call_id}"
    for step in ("in-progress", "completed", "completed", "ringing", "in-progress"):
        assert (
            await _signed(live_harness, status, {"CallSid": sid, "CallStatus": step})
        ).status_code == 204
    for speech in ("Yes please", "No thanks"):
        resp = await _signed(live_harness, gather, {"CallSid": sid, "SpeechResult": speech})
        assert resp.status_code == 200 and "<Hangup/>" in resp.text
    result = (await live_harness.client.get(f"{CALLS}/{call_id}", headers=headers)).json()
    assert result["state"] == "completed" and result["transcript"] == "Yes please"


async def test_calls_are_scoped_to_their_run(
    live_harness: Harness, real_run_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    call_id, _, _ = await _placed(live_harness, real_run_id, user_id)
    other = _agent(live_harness, uuid.uuid4())
    assert (await live_harness.client.get(f"{CALLS}/{call_id}", headers=other)).status_code == 404


async def test_full_number_never_stored(
    harness: Harness,
    real_run_id: uuid.UUID,
    user_id: uuid.UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _configure(harness, user_id)
    await _call(harness, _agent(harness, real_run_id))
    async with sessions() as db:
        rows = (await db.execute(text("SELECT * FROM telephony_calls"))).all()
        events = (await db.execute(text("SELECT * FROM audit_events"))).all()
    assert rows and TO not in str(rows) and TO[1:] not in str(rows)
    assert TO not in str(events)
