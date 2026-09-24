import asyncio
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI
from helpers import SDK, events, manifest, run_state, start

PERMISSIONS: dict[str, Any] = {
    "userInput": True,
    "llmProfiles": ["local.general", "cloud.openai.gpt-small"],
    "cloudProviders": ["openai"],
    "knowledge": ["search"],
    "connectors": {"google": ["gmail.readonly", "spreadsheets"], "twilio": ["voice.call"]},
}


def full_manifest() -> dict[str, Any]:
    m = manifest(**PERMISSIONS)
    m["spec"]["configurationSchema"]["properties"]["knowledgeBaseId"] = {
        "type": "string",
        "x-crewquarters-widget": "knowledgeBase",
        "default": "kb-1",
    }
    return m


def scenario(root: Path) -> Path:
    (root / "kb").mkdir()
    (root / "kb" / "terms.md").write_text("# Refunds\n\nRefunds are available within 30 days.\n")
    (root / "kb2").mkdir()
    (root / "kb2" / "other.md").write_text("Secret roadmap.\n")
    (root / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "connectors",
                "timezone": "UTC",
                "gmail": {
                    "mailbox": [
                        {
                            "id": "m1",
                            "subject": "one",
                            "date": "2026-09-23T10:00:00+00:00",
                            "body": {"text": "a"},
                        },
                        {
                            "id": "m2",
                            "subject": "two",
                            "date": "2026-09-23T11:00:00+00:00",
                            "body": {"text": "b"},
                        },
                    ]
                },
                "sheets": {"spreadsheets": {"s1": {"Contacts": [["name"], ["Asha"]], "Results": []}}},
                "twilio": {"outcomes": {"+15555550101": {"status": "completed", "speech": "yes"}}},
                "llm": {
                    "rules": [
                        {
                            "name": "hello",
                            "match": {"contains": ["hello"]},
                            "respond": {"text": "Hi there friend"},
                        }
                    ]
                },
                "knowledge": {"kb-1": "kb", "kb-2": "kb2"},
            }
        )
    )
    return root


async def load(api: httpx.AsyncClient, root: Path) -> None:
    r = await api.post("/fake/v1/scenarios/load", json={"path": str(scenario(root))})
    assert r.status_code == 200, r.text


async def test_gmail_list_and_get_are_capability_gated(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    listed = await api.get(f"{SDK}/google/gmail/messages", params={"q": "after:0"}, headers=started.headers)
    assert [m["id"] for m in listed.json()["messages"]] == ["m2", "m1"]
    got = await api.get(f"{SDK}/google/gmail/messages/m1", headers=started.headers)
    assert got.json()["id"] == "m1"
    bad = await api.get(f"{SDK}/google/gmail/messages", params={"q": "from:me"}, headers=started.headers)
    assert bad.status_code == 400
    calls = [e for e in await events(api, started.run_id) if e["type"] == "connector.call"]
    assert [(c["payload"]["operation"], c["payload"]["outcome"]) for c in calls] == [
        ("broker.gmail.list", "ok"),
        ("broker.gmail.get", "ok"),
        ("broker.gmail.list", "error"),
    ]
    assert set(calls[0]["payload"]) == {"connector", "operation", "outcome", "requestId"}

    plain = await start(api)
    denied = await api.get(f"{SDK}/google/gmail/messages", headers=plain.headers)
    assert denied.status_code == 403


async def test_expired_google_connection_needs_reconnect(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    await api.post("/fake/v1/connections", json={"provider": "google", "status": "expired"})
    started = await start(api, full_manifest())
    r = await api.get(f"{SDK}/google/gmail/messages", headers=started.headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NEEDS_CONNECTION"


async def test_sheets_get_update_append(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    got = await api.post(
        f"{SDK}/google/sheets/values:get",
        json={"spreadsheetId": "s1", "range": "Contacts!A2:A"},
        headers=started.headers,
    )
    assert got.json()["values"] == [["Asha"]]
    updated = await api.post(
        f"{SDK}/google/sheets/values:update",
        json={"spreadsheetId": "s1", "range": "Results!A2:B2", "values": [["2", "Asha"]]},
        headers=started.headers,
    )
    assert updated.json() == {"updatedRange": "Results!A2:B2", "updatedRows": 1}
    appended = await api.post(
        f"{SDK}/google/sheets/values:append",
        json={"spreadsheetId": "s1", "range": "Results!A:B", "values": [["3", "Ben"]]},
        headers=started.headers,
    )
    assert appended.json()["updatedRange"] == "Results!A3:B3"
    state = (await api.get("/fake/v1/state/sheets")).json()
    assert state["s1"]["Results"][1:] == [["2", "Asha"], ["3", "Ben"]]


async def test_apply_then_drop_on_sheets_append_applies_the_write(
    api: httpx.AsyncClient, tmp_path: Path
) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    await api.post("/fake/v1/faults", json={"target": "broker.sheets.append", "mode": "apply-then-drop"})
    r = await api.post(
        f"{SDK}/google/sheets/values:append",
        json={"spreadsheetId": "s1", "range": "Results!A:B", "values": [["x"]]},
        headers=started.headers,
    )
    assert r.status_code == 504
    assert (await api.get("/fake/v1/state/sheets")).json()["s1"]["Results"] == [["x"]]


async def test_telephony_create_is_deduplicated_and_progresses(
    api: httpx.AsyncClient, tmp_path: Path
) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    body = {
        "to": "+15555550101",
        "script": {"disclosure": "Automated call.", "text": "Hello"},
        "gather": {"input": "speech", "timeoutSeconds": 10},
        "idempotencyKey": "call:1",
    }
    first = await api.post(f"{SDK}/telephony/calls", json=body, headers=started.headers)
    second = await api.post(f"{SDK}/telephony/calls", json=body, headers=started.headers)
    assert first.json()["id"] == second.json()["id"]
    assert "+15555550101" not in first.text
    states = []
    for _ in range(5):
        call = (await api.get(f"{SDK}/telephony/calls/{first.json()['id']}", headers=started.headers)).json()
        states.append(call["state"])
    assert states[-1] == "completed"
    assert call["transcript"] == "yes"
    calls = (await api.get("/fake/v1/state/calls")).json()
    assert calls["byNumber"] == {"+15555550101": 1}
    invalid = await api.post(
        f"{SDK}/telephony/calls", json={**body, "to": "5550101"}, headers=started.headers
    )
    assert invalid.status_code == 422


async def test_llm_chat_resolves_rules_and_records_usage(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    r = await api.post(
        f"{SDK}/llm/chat",
        json={"profile": "local.general.small", "messages": [{"role": "user", "content": "say hello"}]},
        headers={**started.headers, "X-Request-Id": "req-1"},
    )
    body = r.json()
    assert body["text"] == "Hi there friend"
    assert (body["locality"], body["provider"], body["finishReason"]) == ("local", "mock-local", "stop")
    assert body["requestId"] == "req-1"
    llm_events = [e for e in await events(api, started.run_id) if e["type"] == "llm.call"]
    assert llm_events[0]["payload"]["profile"] == "local.general.small"
    assert "messages" not in llm_events[0]["payload"]
    log = (await api.get("/fake/v1/state/llm")).json()
    assert log[0]["messages"][0]["content"] == "say hello"


async def test_llm_profile_must_be_granted(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    r = await api.post(
        f"{SDK}/llm/chat",
        json={"profile": "local.general.quality", "messages": [{"role": "user", "content": "x"}]},
        headers=started.headers,
    )
    assert r.status_code == 403
    cloud = await api.post(
        f"{SDK}/llm/chat",
        json={"profile": "cloud.openai.gpt-small", "messages": [{"role": "user", "content": "x"}]},
        headers=started.headers,
    )
    assert cloud.json()["locality"] == "cloud"
    assert cloud.json()["provider"] == "openai"


async def test_llm_tools_are_unsupported(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    r = await api.post(
        f"{SDK}/llm/chat",
        json={
            "profile": "local.general.small",
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "call"}],
        },
        headers=started.headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNSUPPORTED_FEATURE"


async def test_llm_idempotency_key_returns_cached_response(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    body = {
        "profile": "local.general.small",
        "messages": [{"role": "user", "content": "hello"}],
        "idempotencyKey": "k",
    }
    a = await api.post(f"{SDK}/llm/chat", json=body, headers=started.headers)
    b = await api.post(f"{SDK}/llm/chat", json=body, headers=started.headers)
    assert a.json() == b.json()
    assert len((await api.get("/fake/v1/state/llm")).json()) == 1


async def test_cold_start_moves_run_through_loading_model(
    app: FastAPI, api: httpx.AsyncClient, tmp_path: Path
) -> None:
    await load(api, tmp_path)
    app.state.store.gateway.cold_start_seconds = 0.2
    started = await start(api, full_manifest())
    chat = asyncio.create_task(
        api.post(
            f"{SDK}/llm/chat",
            json={"profile": "local.general.small", "messages": [{"role": "user", "content": "x"}]},
            headers=started.headers,
        )
    )
    await asyncio.sleep(0.05)
    assert await run_state(api, started.run_id) == "LOADING_MODEL"
    await chat
    assert await run_state(api, started.run_id) == "RUNNING"
    await api.post(
        f"{SDK}/llm/chat",
        json={"profile": "local.general.small", "messages": [{"role": "user", "content": "y"}]},
        headers=started.headers,
    )
    statuses = [e["payload"]["to"] for e in await events(api, started.run_id) if e["type"] == "status"]
    assert statuses.count("LOADING_MODEL") == 1


async def test_llm_stream_yields_deltas_then_done(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    r = await api.post(
        f"{SDK}/llm/chat:stream",
        json={"profile": "local.general.small", "messages": [{"role": "user", "content": "hello"}]},
        headers=started.headers,
    )
    assert r.headers["content-type"].startswith("text/event-stream")
    events_seen = [line.removeprefix("event: ") for line in r.text.splitlines() if line.startswith("event: ")]
    assert events_seen[-1] == "done"
    assert set(events_seen[:-1]) == {"delta"}


async def test_knowledge_search_is_scoped_to_granted_bases(api: httpx.AsyncClient, tmp_path: Path) -> None:
    await load(api, tmp_path)
    started = await start(api, full_manifest())
    ok = await api.post(
        f"{SDK}/knowledge/search",
        json={"knowledgeBaseId": "kb-1", "query": "refunds"},
        headers=started.headers,
    )
    assert ok.json()["passages"][0]["document"]["id"] == "terms"
    other = await api.post(
        f"{SDK}/knowledge/search",
        json={"knowledgeBaseId": "kb-2", "query": "roadmap"},
        headers=started.headers,
    )
    assert other.status_code == 403
