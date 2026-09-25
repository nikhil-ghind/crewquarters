"""Security review regressions (docs/security-review-person3.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from crewquarters_broker.config import BrokerSettings
from pydantic import ValidationError

SDK = "/internal/v1/sdk"


@pytest.mark.parametrize("key", [".", "..", ".hidden", "-x", ":x"])
async def test_action_keys_cannot_be_dot_segments(harness: Any, key: str) -> None:
    headers = harness.agent(["idempotency"])
    resp = await harness.client.post(f"{SDK}/actions/{key}/claim", headers=headers)
    assert resp.status_code in (404, 422), resp.text
    assert harness.control_requests == []


async def test_declared_oversized_body_is_refused(harness: Any) -> None:
    headers = harness.agent(["events.write"])
    body = b'{"events": []}' + b" " * (harness.settings.max_body_bytes + 1)
    resp = await harness.client.post(
        f"{SDK}/events", headers={**headers, "content-type": "application/json"}, content=body
    )
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert harness.control_requests == []


async def test_streamed_oversized_body_is_refused(harness: Any) -> None:
    headers = harness.agent(["events.write"])
    chunk = b" " * 65_536

    async def stream() -> AsyncIterator[bytes]:
        for _ in range(harness.settings.max_body_bytes // len(chunk) + 2):
            yield chunk

    resp = await harness.client.post(
        f"{SDK}/events", headers={**headers, "content-type": "application/json"}, content=stream()
    )
    assert resp.status_code == 413


async def test_configured_knowledge_base_must_be_a_uuid(harness: Any) -> None:
    headers = harness.agent(["knowledge.search:config"], config={"knowledgeBaseId": "../admin"})
    resp = await harness.client.post(
        f"{SDK}/knowledge/search",
        headers=headers,
        json={"knowledgeBaseId": "../admin", "query": "q"},
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"
    assert harness.knowledge_requests == []


@pytest.mark.no_db
def test_live_mode_requires_https() -> None:
    with pytest.raises(ValidationError, match="https"):
        BrokerSettings(provider_mode="live", public_base_url="http://demo.example.com")
    for ok in ("https://demo.example.com", "http://localhost:8080", "http://127.0.0.1:8080"):
        BrokerSettings(provider_mode="live", public_base_url=ok)
    BrokerSettings(provider_mode="fake", public_base_url="http://demo.example.com")


async def test_only_health_and_signed_callbacks_are_unauthenticated(harness: Any) -> None:
    for path in (
        "/openapi.json",
        "/docs",
        "/redoc",
        "/internal/v1/connections",
        "/internal/v1/provider-profiles",
        "/internal/v1/metrics",
    ):
        assert (await harness.client.get(path)).status_code in (401, 404), path
    assert (await harness.client.get("/health/live")).status_code == 200
