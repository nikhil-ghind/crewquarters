"""Broker voice-call routes with the offline backend (no LiveKit): states, idempotency, masking."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fake_helpers import SDK, audit, manifest, start
from fastapi import FastAPI

from crewquarters_fake.voice.scenario import parse_callees

VOICE = f"{SDK}/voice/calls"
NUMBER = "+15555550101"


def voice_manifest(agent_id: str = "probe") -> dict[str, Any]:
    m = manifest(
        llmProfiles=["local.general", "local.stt", "local.tts"],
        connectors={"google": [], "twilio": [], "sip": ["call.conversational"]},
        userInput=True,
    )
    m["metadata"]["id"] = agent_id
    return m


def callees(app: FastAPI, spec: dict[str, Any]) -> None:
    app.state.store.voice_callees = parse_callees(spec)


async def dial(api: httpx.AsyncClient, headers: dict[str, str], **extra: Any) -> httpx.Response:
    body = {"to": NUMBER, "idempotencyKey": "voice:row-2", **extra}
    return await api.post(VOICE, json=body, headers=headers)


async def wait_for_state(app: FastAPI, call_id: str, state: str) -> None:
    store = app.state.store
    reached = await store.wait_until(lambda: store.voice_calls[call_id].state == state, 3)
    assert reached, f"call stayed {store.voice_calls[call_id].state}, expected {state}"


async def test_dial_requires_the_sip_capability(api: httpx.AsyncClient) -> None:
    started = await start(api, manifest(userInput=True))
    r = await dial(api, started.headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CAPABILITY_DENIED"
    [denied] = [a for a in await audit(api) if a["action"] == "capability.denied"]
    assert denied["capability"] == "sip.call.conversational"


async def test_dial_masks_the_number_and_returns_a_room(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    callees(app, {NUMBER: {"outcome": "answer", "ringSeconds": 0.05}})
    started = await start(api, voice_manifest())
    r = await dial(api, started.headers)
    assert r.status_code == 200, r.text
    call = r.json()
    assert call["toMasked"] == "••••0101"
    assert NUMBER not in r.text and "5555550101" not in r.text
    assert set(call["room"]) == {"url", "name", "token", "identity"}
    assert call["room"]["identity"] == "agent"
    assert call["state"] in {"dialing", "ringing"}


async def test_dial_is_idempotent_by_key(app: FastAPI, api: httpx.AsyncClient) -> None:
    callees(app, {NUMBER: {"outcome": "answer", "ringSeconds": 0.05}})
    started = await start(api, voice_manifest())
    first, again = await dial(api, started.headers), await dial(api, started.headers)
    assert first.json()["id"] == again.json()["id"]
    assert len(app.state.store.voice_calls) == 1


async def test_answered_call_hangs_up_as_completed(app: FastAPI, api: httpx.AsyncClient) -> None:
    callees(app, {NUMBER: {"outcome": "answer", "ringSeconds": 0.05}})
    started = await start(api, voice_manifest())
    call = (await dial(api, started.headers)).json()
    await wait_for_state(app, call["id"], "answered")
    current = (await api.get(f"{VOICE}/{call['id']}", headers=started.headers)).json()
    assert current["state"] == "answered" and current["answeredAt"]
    ended = (await api.post(f"{VOICE}/{call['id']}/hangup", headers=started.headers)).json()
    assert ended["state"] == "completed"
    assert ended["endedAt"] and ended["durationSeconds"] >= 0
    again = (await api.post(f"{VOICE}/{call['id']}/hangup", headers=started.headers)).json()
    assert again["state"] == "completed" and again["endedAt"] == ended["endedAt"]
    actions = [a["action"] for a in await audit(api)]
    assert "voice.dial" in actions and "voice.hangup" in actions


@pytest.mark.parametrize("outcome", ["busy", "no-answer", "failed"])
async def test_unanswered_outcomes(app: FastAPI, api: httpx.AsyncClient, outcome: str) -> None:
    callees(app, {NUMBER: {"outcome": outcome, "ringSeconds": 0.05}})
    started = await start(api, voice_manifest())
    call = (await dial(api, started.headers)).json()
    await wait_for_state(app, call["id"], outcome)
    ended = (await api.get(f"{VOICE}/{call['id']}", headers=started.headers)).json()
    assert ended["endedAt"] and ended["answeredAt"] is None


async def test_unknown_number_is_not_answered(app: FastAPI, api: httpx.AsyncClient) -> None:
    callees(app, {})
    started = await start(api, voice_manifest())
    call = (await dial(api, started.headers)).json()
    await wait_for_state(app, call["id"], "no-answer")


async def test_hangup_while_ringing_cancels(app: FastAPI, api: httpx.AsyncClient) -> None:
    callees(app, {NUMBER: {"outcome": "answer", "ringSeconds": 30}})
    started = await start(api, voice_manifest())
    call = (await dial(api, started.headers)).json()
    ended = (await api.post(f"{VOICE}/{call['id']}/hangup", headers=started.headers)).json()
    assert ended["state"] == "canceled" and ended["answeredAt"] is None


async def test_invalid_number_is_rejected(api: httpx.AsyncClient) -> None:
    started = await start(api, voice_manifest())
    r = await dial(api, started.headers, to="5550101")
    assert r.status_code == 422


async def test_other_runs_cannot_see_the_call(app: FastAPI, api: httpx.AsyncClient) -> None:
    callees(app, {NUMBER: {"outcome": "answer", "ringSeconds": 0.05}})
    first = await start(api, voice_manifest())
    call = (await dial(api, first.headers)).json()
    other = await start(api, voice_manifest("other-agent"))
    r = await api.get(f"{VOICE}/{call['id']}", headers=other.headers)
    assert r.status_code == 404


async def test_admin_state_lists_calls_without_full_numbers(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    callees(app, {NUMBER: {"outcome": "busy", "ringSeconds": 0.05}})
    started = await start(api, voice_manifest())
    await dial(api, started.headers)
    state = await api.get("/fake/v1/state/voice")
    assert state.json()[0]["toMasked"] == "••••0101"
    assert "5555550101" not in state.text


async def test_answered_call_ends_at_its_maximum_duration(app: FastAPI) -> None:
    from crewquarters_fake.voice.offline import OfflineVoiceBackend

    store = app.state.store
    store.voice_callees = parse_callees({NUMBER: {"outcome": "answer", "ringSeconds": 0.01}})
    call = store.new_voice_call("run-1", "k", NUMBER, ring_timeout=5, max_duration=0.1)
    await OfflineVoiceBackend().dial(store, call)
    assert await store.wait_until(lambda: call.state == "completed", 3)
    assert call.error_code == "MAX_DURATION"
