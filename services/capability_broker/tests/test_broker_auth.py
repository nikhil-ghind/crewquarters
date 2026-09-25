"""Capability-token checks: signature, revocation, run state, and permission intersection."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import jwt

from crewquarters_shared import capability

if TYPE_CHECKING:
    from broker_testkit import Harness

pytest_plugins = ["broker_testkit"]

GMAIL = "/agent/v1/google/gmail/messages"


async def test_missing_or_malformed_token(harness: Harness) -> None:
    for headers in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer not-a-jwt"}):
        resp = await harness.client.get(GMAIL, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_token_signed_with_another_key(harness: Harness) -> None:
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


async def test_expired_token(harness: Harness) -> None:
    run_id = uuid.uuid4()
    token, _ = capability.mint(
        signing_key=harness.settings.capability_signing_key.get_secret_value(),
        run_id=run_id,
        attempt=1,
        installation_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        capabilities=["google.gmail.readonly"],
        ttl_seconds=-10,
    )
    resp = await harness.client.get(GMAIL, headers={"authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_replaced_token_is_revoked(harness: Harness) -> None:
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["capabilityTokenId"] = "a-newer-attempt-token"
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.status_code == 401
    assert "revoked" in resp.json()["error"]["message"]


async def test_stale_attempt_and_other_installation(harness: Harness) -> None:
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["currentAttempt"] = 2
    assert (await harness.client.get(GMAIL, headers=headers)).status_code == 401
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["installationId"] = str(uuid.uuid4())
    assert (await harness.client.get(GMAIL, headers=headers)).status_code == 401


async def test_unknown_run(harness: Harness) -> None:
    headers = harness.agent(["google.gmail.readonly"])
    harness.run["id"] = str(uuid.uuid4())  # control API now answers 404 for the token's run
    assert (await harness.client.get(GMAIL, headers=headers)).status_code == 401


async def test_inactive_run(harness: Harness) -> None:
    for state in ("QUEUED", "SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"):
        headers = harness.agent(["google.gmail.readonly"], state=state)
        resp = await harness.client.get(GMAIL, headers=headers)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RUN_NOT_ACTIVE"


async def test_capability_not_in_token(harness: Harness) -> None:
    headers = harness.agent(["google.spreadsheets"])
    resp = await harness.client.get(GMAIL, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"] == {
        "code": "PERMISSION_DENIED",
        "message": "This run is not allowed to use google.gmail.readonly.",
        "requestId": resp.headers["x-request-id"],
        "details": {"capability": "google.gmail.readonly"},
    }


async def test_capability_revoked_by_narrower_approval(harness: Harness) -> None:
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


async def test_every_agent_route_requires_its_capability(harness: Harness) -> None:
    headers = harness.agent(["events.write"])  # nothing else granted
    calls = [
        ("GET", GMAIL, None),
        ("GET", f"{GMAIL}/abc", None),
        ("GET", "/agent/v1/google/sheets/values?range=A1", None),
        ("POST", "/agent/v1/google/sheets/values:append", {"range": "A1", "values": [["x"]]}),
        ("PUT", "/agent/v1/google/sheets/values", {"range": "A1", "values": [["x"]]}),
        ("POST", "/agent/v1/telephony/calls", {"to": "+15555550101", "idempotencyKey": "k"}),
        ("GET", f"/agent/v1/telephony/calls/{uuid.uuid4()}", None),
        ("POST", "/agent/v1/knowledge/search", {"query": "q"}),
        (
            "POST",
            "/agent/v1/input-requests",
            {"key": "k", "title": "t", "prompt": "p", "schema": {}, "timeoutSeconds": 60},
        ),
        ("GET", f"/agent/v1/input-requests/{uuid.uuid4()}", None),
        ("POST", "/agent/v1/actions/k/claim", None),
        ("POST", "/agent/v1/actions/k/complete", {"result": 1}),
    ]
    for method, path, body in calls:
        resp = await harness.client.request(method, path, headers=headers, json=body)
        assert resp.status_code == 403, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_knowledge_search_uses_configured_base_only(harness: Harness) -> None:
    headers = harness.agent(["knowledge.search:config"], config={"knowledgeBaseId": harness.KB_ID})
    resp = await harness.client.post(
        "/agent/v1/knowledge/search",
        headers=headers,
        json={"query": "cancellation terms", "topK": 4, "filters": {"knowledgeBaseId": "other"}},
    )
    assert resp.status_code == 200, resp.text
    [sent] = harness.knowledge_requests
    assert sent["path"] == f"/internal/v1/knowledge-bases/{harness.KB_ID}/query"
    assert sent["body"]["topK"] == 4 and sent["body"]["query"] == "cancellation terms"


async def test_knowledge_without_configured_base(harness: Harness) -> None:
    headers = harness.agent(["knowledge.search:config"], config={})
    resp = await harness.client.post(
        "/agent/v1/knowledge/search", headers=headers, json={"query": "q"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"
    assert harness.knowledge_requests == []
