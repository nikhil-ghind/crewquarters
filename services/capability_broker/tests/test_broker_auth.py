"""Capability-token checks: signature, revocation, run state, and permission intersection."""

from __future__ import annotations

import uuid
from typing import Any

import jwt

from crewquarters_shared import capability

SDK = "/internal/v1/sdk"
GMAIL = f"{SDK}/google/gmail/messages"


async def test_missing_or_malformed_token(harness: Any) -> None:
    for headers in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer not-a-jwt"}):
        resp = await harness.client.get(GMAIL, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_token_signed_with_another_key(harness: Any) -> None:
    harness.agent(["google.gmail.readonly"])
    forged = jwt.encode(
        {
            "aud": capability.AUDIENCE,
            "iss": capability.ISSUER,
            "exp": 9_999_999_999,
            "iat": 1,
            "jti": harness.run["capabilityTokenId"],
            "run": harness.run["id"],
            "att": 1,
            "ins": harness.run["installationId"],
            "ver": "v",
            "cap": ["google.gmail.readonly"],
        },
        "some-other-key-that-is-long-enough-000",
        algorithm="HS256",
    )
    resp = await harness.client.get(GMAIL, headers={"authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


async def test_expired_token(harness: Any) -> None:
    token, _ = capability.mint(
        signing_key=harness.settings.capability_signing_key.get_secret_value(),
        run_id=uuid.uuid4(),
        attempt=1,
        installation_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        capabilities=["google.gmail.readonly"],
        ttl_seconds=-10,
    )
    resp = await harness.client.get(GMAIL, headers={"authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_replaced_token_is_revoked(harness: Any) -> None:
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["capabilityTokenId"] = "a-newer-attempt-token"
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.status_code == 401
    assert "revoked" in resp.json()["error"]["message"]


async def test_stale_attempt_and_other_installation(harness: Any) -> None:
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["currentAttempt"] = 2
    assert (await harness.client.get(GMAIL, headers=headers)).status_code == 401
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["installationId"] = str(uuid.uuid4())
    assert (await harness.client.get(GMAIL, headers=headers)).status_code == 401


async def test_unknown_run(harness: Any) -> None:
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["id"] = str(uuid.uuid4())  # the control API now answers 404 for the token's run
    assert (await harness.client.get(GMAIL, headers=headers)).status_code == 401


async def test_inactive_run(harness: Any) -> None:
    for state in ("QUEUED", "SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"):
        headers = harness.agent(["google.gmail.readonly"], state=state)
        resp = await harness.client.get(GMAIL, headers=headers)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RUN_NOT_ACTIVE"


async def test_capability_not_in_token(harness: Any) -> None:
    headers = harness.agent(["google.spreadsheets"])
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"] == {
        "code": "CAPABILITY_DENIED",
        "message": "This run is not allowed to use google.gmail.readonly.",
        "requestId": resp.headers["x-request-id"],
        "details": {"capability": "google.gmail.readonly"},
    }


async def test_capability_revoked_by_narrower_approval(harness: Any) -> None:
    """The token still lists gmail, but the current approval no longer does."""
    headers = harness.agent(
        ["google.gmail.readonly"],
        permissions={
            "llmProfiles": [],
            "knowledge": [],
            "connectors": {"google": []},
            "cloudProviders": [],
            "userInput": False,
        },
    )
    assert (await harness.client.get(GMAIL, headers=headers)).status_code == 403


CAPABILITY_OPERATIONS = [
    ("GET", GMAIL, None),
    ("GET", f"{GMAIL}/abc", None),
    ("POST", f"{SDK}/google/sheets/values:get", {"spreadsheetId": "s", "range": "A1"}),
    (
        "POST",
        f"{SDK}/google/sheets/values:update",
        {"spreadsheetId": "s", "range": "A1", "values": [["x"]]},
    ),
    (
        "POST",
        f"{SDK}/google/sheets/values:append",
        {"spreadsheetId": "s", "range": "A1", "values": [["x"]]},
    ),
    (
        "POST",
        f"{SDK}/telephony/calls",
        {
            "to": "+15555550101",
            "script": {"disclosure": "d", "text": "t"},
            "gather": {"input": "speech", "timeoutSeconds": 10},
            "idempotencyKey": "k",
        },
    ),
    ("GET", f"{SDK}/telephony/calls/{uuid.uuid4()}", None),
    ("POST", f"{SDK}/knowledge/search", {"knowledgeBaseId": "kb", "query": "q"}),
    (
        "POST",
        f"{SDK}/llm/chat",
        {"profile": "local.general", "messages": [{"role": "user", "content": "hi"}]},
    ),
    (
        "POST",
        f"{SDK}/llm/chat:stream",
        {"profile": "local.general", "messages": [{"role": "user", "content": "hi"}]},
    ),
    (
        "POST",
        f"{SDK}/input-requests",
        {"key": "k", "title": "t", "prompt": "p", "schema": {}, "timeoutSeconds": 60},
    ),
    ("GET", f"{SDK}/input-requests/{uuid.uuid4()}", None),
]


async def test_every_capability_operation_requires_its_capability(harness: Any) -> None:
    headers = harness.agent(["events.write"])  # nothing else granted
    for method, path, body in CAPABILITY_OPERATIONS:
        resp = await harness.client.request(method, path, headers=headers, json=body)
        assert resp.status_code == 403, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "CAPABILITY_DENIED"
    assert harness.gateway_requests == [] and harness.knowledge_requests == []


async def test_cancel_stops_capability_operations_but_not_baseline(harness: Any) -> None:
    caps = [
        "events.write",
        "idempotency",
        "user_input",
        "google.gmail.readonly",
        "google.spreadsheets",
        "twilio.call.fixed_script",
        "knowledge.search:config",
        "llm.profile:local.general.small",
    ]
    headers = harness.agent(caps)
    harness.run["cancelRequested"] = True
    for method, path, body in CAPABILITY_OPERATIONS:
        resp = await harness.client.request(method, path, headers=headers, json=body)
        assert resp.status_code == 409, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "RUN_CANCELLED"
    for path in ("/heartbeat", "/actions/row-1/claim"):
        assert (await harness.client.post(SDK + path, headers=headers)).status_code == 200
    event = {
        "clientEventId": "e1",
        "type": "run.log",
        "occurredAt": "2026-09-25T10:00:00Z",
        "payload": {"level": "info", "message": "stopping"},
    }
    resp = await harness.client.post(f"{SDK}/events", headers=headers, json={"events": [event]})
    assert resp.status_code == 200


async def test_knowledge_search_uses_configured_base_only(harness: Any) -> None:
    headers = harness.agent(["knowledge.search:config"], config={"knowledgeBaseId": harness.KB_ID})
    resp = await harness.client.post(
        f"{SDK}/knowledge/search",
        headers=headers,
        json={"knowledgeBaseId": harness.KB_ID, "query": "cancellation terms", "topK": 4},
    )
    assert resp.status_code == 200, resp.text
    [sent] = harness.knowledge_requests
    assert sent["path"] == f"/internal/v1/knowledge-bases/{harness.KB_ID}/query"
    assert sent["body"]["topK"] == 4 and sent["body"]["query"] == "cancellation terms"

    other = await harness.client.post(
        f"{SDK}/knowledge/search",
        headers=headers,
        json={"knowledgeBaseId": str(uuid.uuid4()), "query": "q"},
    )
    assert other.status_code == 403 and other.json()["error"]["code"] == "PERMISSION_DENIED"
    assert len(harness.knowledge_requests) == 1


async def test_knowledge_without_configured_base(harness: Any) -> None:
    headers = harness.agent(["knowledge.search:config"], config={})
    resp = await harness.client.post(
        f"{SDK}/knowledge/search", headers=headers, json={"knowledgeBaseId": "kb", "query": "q"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"
    assert harness.knowledge_requests == []


async def test_invalid_request_code(harness: Any) -> None:
    headers = harness.agent(["knowledge.search:config"], config={"knowledgeBaseId": "kb"})
    resp = await harness.client.post(f"{SDK}/knowledge/search", headers=headers, json={})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_REQUEST"
