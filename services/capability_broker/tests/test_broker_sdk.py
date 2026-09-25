"""SDK contract operations: handshake, event batches, and LLM forwarding to the gateway."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.main import create_app

SDK = "/internal/v1/sdk"
CONTRACT = Path(__file__).parents[3] / "packages/contracts/broker-sdk.openapi.yaml"
CHAT = {"profile": "local.general", "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.no_db
def test_broker_serves_every_contract_operation() -> None:
    contract = yaml.safe_load(CONTRACT.read_text())
    wanted = {
        (method.upper(), SDK + path)
        for path, item in contract["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    paths = create_app(BrokerSettings()).openapi()["paths"]
    served = {
        (method.upper(), re.sub(r"\{[a-z_]*id\}", "{id}", path))
        for path, item in paths.items()
        for method in item
        if path.startswith(SDK)
    }
    assert wanted <= served, sorted(wanted - served)


async def test_handshake_returns_run_context(harness: Any) -> None:
    caps = [
        "events.write",
        "idempotency",
        "google.gmail.readonly",
        "knowledge.search:config",
        "llm.profile:local.general.small",
    ]
    headers = harness.agent(caps, config={"knowledgeBaseId": "kb-1", "timezone": "UTC"})
    resp = await harness.client.post(
        f"{SDK}/handshake",
        headers=headers,
        json={"protocol": "v1alpha1", "sdkVersion": "0.1.0", "agentId": "gmail-digest"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert harness.control_requests[-1] == {
        "path": f"/internal/v1/runs/{harness.run['id']}/handshake",
        "body": {"attempt": 1},
    }
    assert body["run"]["id"] == harness.run["id"] and body["run"]["attempt"] == 1
    assert body["run"]["agentId"] == "gmail-digest" and body["run"]["trigger"] == "manual"
    assert body["config"] == {"knowledgeBaseId": "kb-1", "timezone": "UTC"}
    assert body["capabilities"] == sorted(caps)
    assert body["grants"] == {
        "llmProfiles": ["local.general.small"],
        "modelBindings": {},
        "knowledgeBaseIds": ["kb-1"],
        "google": ["gmail.readonly"],
        "twilio": [],
        "cloudProviders": [],
    }
    assert 0 < body["limits"]["activeTimeoutSeconds"] <= 600
    assert body["heartbeatIntervalSeconds"] > 0 and body["serverTime"]


async def test_handshake_rejects_other_protocols(harness: Any) -> None:
    headers = harness.agent(["events.write"])
    resp = await harness.client.post(
        f"{SDK}/handshake",
        headers=headers,
        json={"protocol": "v2", "sdkVersion": "9", "agentId": "x"},
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "PROTOCOL_UNSUPPORTED"
    assert harness.control_requests == []


async def test_event_batch_is_forwarded_once_per_client_id(harness: Any) -> None:
    headers = harness.agent(["events.write"])

    def event(cid: str) -> dict[str, Any]:
        return {
            "clientEventId": cid,
            "type": "run.progress",
            "occurredAt": "2026-09-25T10:00:00Z",
            "payload": {"percent": 10, "message": cid},
        }

    resp = await harness.client.post(
        f"{SDK}/events", headers=headers, json={"events": [event("a"), event("b"), event("a")]}
    )
    assert resp.json() == {"accepted": 2, "lastSequence": 2}
    assert [r["body"] for r in harness.control_requests] == [
        {"attempt": 1, "type": "run.progress", "payload": {"percent": 10, "message": "a"}},
        {"attempt": 1, "type": "run.progress", "payload": {"percent": 10, "message": "b"}},
    ]


async def test_llm_chat_forwards_token_to_gateway(harness: Any) -> None:
    headers = harness.agent(["llm.profile:local.general.small"])
    resp = await harness.client.post(
        f"{SDK}/llm/chat", headers=headers, json={**CHAT, "idempotencyKey": "k1"}
    )
    assert resp.status_code == 200 and resp.json()["text"] == "Hi."
    [sent] = harness.gateway_requests
    assert sent.url.path == "/internal/v1/llm/chat"
    assert sent.headers["x-capability-token"] == headers["authorization"].removeprefix("Bearer ")
    token = harness.settings.internal_service_token.get_secret_value()
    assert sent.headers["authorization"] == f"Bearer {token}"
    assert json.loads(sent.content) == {
        **CHAT,
        "tools": [],
        "idempotencyKey": "k1",
        "stream": False,
    }


async def test_llm_stream_is_translated_to_contract_events(harness: Any) -> None:
    headers = harness.agent(["llm.profile:local.general.small"])
    resp = await harness.client.post(f"{SDK}/llm/chat:stream", headers=headers, json=CHAT)
    assert resp.headers["content-type"].startswith("text/event-stream")
    blocks = [b for b in resp.text.split("\n\n") if b]
    events = [
        (b.split("\n")[0], json.loads(b.split("\n")[1].removeprefix("data: "))) for b in blocks
    ]
    assert events[:2] == [("event: delta", {"text": "H"}), ("event: delta", {"text": "i."})]
    assert events[2][0] == "event: done" and events[2][1]["finishReason"] == "stop"
    assert json.loads(harness.gateway_requests[0].content)["stream"] is True


async def test_llm_gateway_errors_are_relayed(harness: Any) -> None:
    headers = harness.agent(["llm.profile:local.general.small"])
    harness.gateway_error = (503, {"code": "MODEL_UNAVAILABLE", "message": "Loading failed."})
    for path in ("/llm/chat", "/llm/chat:stream"):
        resp = await harness.client.post(SDK + path, headers=headers, json=CHAT)
        assert resp.status_code == 503 and resp.json()["error"]["code"] == "MODEL_UNAVAILABLE"


async def test_cloud_profile_needs_cloud_capability(harness: Any) -> None:
    headers = harness.agent(["llm.profile:openai.gpt-small"])
    resp = await harness.client.post(
        f"{SDK}/llm/chat",
        headers=headers,
        json={**CHAT, "profile": "openai.gpt-small"},
    )
    assert resp.status_code == 403 and resp.json()["error"]["details"] == {
        "capability": "cloud.openai"
    }
    assert harness.gateway_requests == []


async def test_llm_rejects_tools(harness: Any) -> None:
    headers = harness.agent(["llm.profile:local.general.small"])
    resp = await harness.client.post(
        f"{SDK}/llm/chat", headers=headers, json={**CHAT, "tools": [{"name": "x"}]}
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_REQUEST"
