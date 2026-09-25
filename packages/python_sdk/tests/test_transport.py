from collections.abc import Callable
from typing import Any

import httpx
import pytest

from crewquarters._transport import SDK_PREFIX, BrokerClient
from crewquarters.errors import ModelUnavailable, OutcomeUnknown, PlatformError, ProviderError

BASE = "http://broker.test"


class Recorder:
    def __init__(self, responses: list[Callable[[httpx.Request], httpx.Response]]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)(request)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(self, max_attempts: int = 4) -> BrokerClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return BrokerClient(
            BASE,
            "tok-123",
            http=http,
            max_attempts=max_attempts,
            sleep=self.sleep,
            jitter=lambda: 0.0,
        )


def ok(body: Any = None) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=body if body is not None else {"ok": True})


def err(
    status: int, code: str, headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(
        status, json={"error": {"code": code, "message": code.lower()}}, headers=headers or {}
    )


def raise_(exc_type: type[httpx.TransportError]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("simulated", request=request)

    return handler


async def test_success_returns_json_and_sends_auth_and_request_id() -> None:
    rec = Recorder([ok({"a": 1}), ok({"a": 2})])
    client = rec.client()
    assert await client.request(
        "POST", "/handshake", operation="handshake", idempotent=True, json={}
    ) == {"a": 1}
    await client.request("POST", "/handshake", operation="handshake", idempotent=True, json={})
    first, second = rec.requests
    assert str(first.url) == f"{BASE}{SDK_PREFIX}/handshake"
    assert first.headers["Authorization"] == "Bearer tok-123"
    assert first.headers["X-Request-Id"] != second.headers["X-Request-Id"]


async def test_idempotent_503_retries_then_succeeds() -> None:
    rec = Recorder([err(503, "PROVIDER_UNAVAILABLE"), err(503, "PROVIDER_UNAVAILABLE"), ok()])
    assert await rec.client().request("GET", "/x", operation="x", idempotent=True) == {"ok": True}
    assert len(rec.requests) == 3
    assert rec.sleeps == [0.5, 1.0]


async def test_non_idempotent_503_is_not_retried() -> None:
    rec = Recorder([err(503, "PROVIDER_UNAVAILABLE")])
    with pytest.raises(ProviderError):
        await rec.client().request("POST", "/x", operation="x", idempotent=False, json={})
    assert len(rec.requests) == 1


async def test_non_idempotent_504_raises_outcome_unknown() -> None:
    rec = Recorder([err(504, "TIMEOUT")])
    with pytest.raises(OutcomeUnknown):
        await rec.client().request("POST", "/x", operation="x", idempotent=False, json={})
    assert len(rec.requests) == 1


async def test_idempotent_read_timeout_retries() -> None:
    rec = Recorder([raise_(httpx.ReadTimeout), ok()])
    assert await rec.client().request("GET", "/x", operation="x", idempotent=True) == {"ok": True}
    assert len(rec.requests) == 2


async def test_non_idempotent_read_timeout_raises_outcome_unknown() -> None:
    rec = Recorder([raise_(httpx.ReadTimeout)])
    with pytest.raises(OutcomeUnknown):
        await rec.client().request("POST", "/x", operation="x", idempotent=False, json={})
    assert len(rec.requests) == 1


async def test_connect_error_is_retried_even_when_not_idempotent() -> None:
    rec = Recorder([raise_(httpx.ConnectError), ok()])
    assert await rec.client().request("POST", "/x", operation="x", idempotent=False, json={}) == {
        "ok": True
    }
    assert len(rec.requests) == 2


async def test_rate_limit_honours_retry_after() -> None:
    rec = Recorder([err(429, "RATE_LIMITED", {"Retry-After": "3"}), ok()])
    await rec.client().request("GET", "/x", operation="x", idempotent=True)
    assert rec.sleeps == [3.0]


async def test_model_unavailable_is_not_retried() -> None:
    rec = Recorder([err(503, "MODEL_UNAVAILABLE")])
    with pytest.raises(ModelUnavailable):
        await rec.client().request(
            "POST", "/llm/chat", operation="llm.chat", idempotent=True, json={}
        )
    assert len(rec.requests) == 1


async def test_exhausted_retries_raise_retryable_error() -> None:
    rec = Recorder([err(503, "PROVIDER_UNAVAILABLE")] * 3)
    with pytest.raises(ProviderError) as info:
        await rec.client(max_attempts=3).request("GET", "/x", operation="x", idempotent=True)
    assert info.value.retryable is True
    assert len(rec.requests) == 3


async def test_unreachable_broker_raises_platform_error() -> None:
    rec = Recorder([raise_(httpx.ConnectError)] * 2)
    with pytest.raises(PlatformError) as info:
        await rec.client(max_attempts=2).request(
            "POST", "/handshake", operation="handshake", idempotent=True
        )
    assert info.value.code == "BROKER_UNAVAILABLE"


async def test_backoff_is_capped_at_eight_seconds() -> None:
    rec = Recorder([err(503, "PROVIDER_UNAVAILABLE")] * 7 + [ok()])
    await rec.client(max_attempts=8).request("GET", "/x", operation="x", idempotent=True)
    assert rec.sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


async def test_empty_body_returns_none() -> None:
    rec = Recorder([lambda request: httpx.Response(204)])
    assert await rec.client().request("POST", "/x", operation="x", idempotent=True) is None


async def test_stream_sse_yields_events() -> None:
    body = (
        b'event: delta\ndata: {"text": "Hel"}\n\n'
        b'event: delta\ndata: {"text": "lo"}\n\n'
        b": comment\n"
        b'event: done\ndata: {"text": "Hello"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BrokerClient(BASE, "t", http=http)
    events = [
        e async for e in client.stream_sse("/llm/chat:stream", operation="llm.stream", json={})
    ]
    assert events == [
        ("delta", {"text": "Hel"}),
        ("delta", {"text": "lo"}),
        ("done", {"text": "Hello"}),
    ]


async def test_stream_sse_error_status_raises_mapped_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "MODEL_UNAVAILABLE", "message": "down"}})

    client = BrokerClient(BASE, "t", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelUnavailable):
        async for _ in client.stream_sse("/llm/chat:stream", operation="llm.stream", json={}):
            pass
