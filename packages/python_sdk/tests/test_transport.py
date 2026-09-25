from collections.abc import Callable
from typing import Any

import httpx
import pytest

from crewquarters._transport import SDK_PREFIX, BrokerClient, outage_budget
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

    def client(self, max_attempts: int = 4, outage_budget: float = 25.0) -> BrokerClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return BrokerClient(
            BASE,
            "tok-123",
            http=http,
            max_attempts=max_attempts,
            sleep=self.sleep,
            jitter=lambda: 0.0,
            outage_budget=outage_budget,
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


async def test_unreachable_broker_raises_platform_error_once_the_outage_budget_is_spent() -> None:
    rec = Recorder([raise_(httpx.ConnectError)] * 3)
    with pytest.raises(PlatformError) as info:
        await rec.client(outage_budget=1.0).request(
            "POST", "/handshake", operation="handshake", idempotent=True
        )
    assert info.value.code == "BROKER_UNAVAILABLE" and info.value.retryable is True
    assert len(rec.requests) == 3
    assert rec.sleeps == [0.5, 0.5]  # the last sleep is cut to the remaining budget


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


class Outage:
    """A broker that is down (``failure`` for every request) until ``down_for`` seconds of a
    fake clock have passed. The injected sleep advances the clock."""

    def __init__(self, down_for: float, failure: Callable[[httpx.Request], httpx.Response]) -> None:
        self.down_for = down_for
        self.failure = failure
        self.now = 0.0
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.now < self.down_for:
            return self.failure(request)
        return httpx.Response(200, json={"ok": True})

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def client(self, outage_budget: float = 25.0) -> BrokerClient:
        return BrokerClient(
            BASE,
            "tok",
            http=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
            sleep=self.sleep,
            jitter=lambda: 0.1,
            clock=lambda: self.now,
            outage_budget=outage_budget,
        )


@pytest.mark.parametrize("idempotent", [True, False])
async def test_a_six_second_broker_restart_is_ridden_out(idempotent: bool) -> None:
    """Connection refused for 6 s (a restart): nothing was sent, so even a non-idempotent call
    is retried, with capped backoff and jitter, and succeeds once the broker is back."""
    outage = Outage(6.0, raise_(httpx.ConnectError))
    body = await outage.client().request(
        "POST", "/x", operation="x", idempotent=idempotent, json={}
    )
    assert body == {"ok": True}
    assert outage.sleeps == pytest.approx([0.6, 1.1, 2.1, 4.1])  # capped at 4 s + jitter
    assert 6.0 <= outage.now < 25.0


async def test_an_idempotent_call_rides_out_connections_dropped_mid_request() -> None:
    outage = Outage(6.0, raise_(httpx.RemoteProtocolError))
    assert await outage.client().request("GET", "/x", operation="x", idempotent=True) == {
        "ok": True
    }
    assert len(outage.requests) == 5


async def test_a_non_idempotent_call_whose_connection_dropped_is_outcome_unknown() -> None:
    outage = Outage(6.0, raise_(httpx.ReadError))
    with pytest.raises(OutcomeUnknown):
        await outage.client().request("POST", "/x", operation="x", idempotent=False, json={})
    assert len(outage.requests) == 1


async def test_an_outage_longer_than_the_budget_fails_at_the_budget() -> None:
    outage = Outage(3600.0, raise_(httpx.ConnectError))
    with pytest.raises(PlatformError) as info:
        await outage.client().request("GET", "/x", operation="x", idempotent=True)
    assert info.value.code == "BROKER_UNAVAILABLE"
    assert outage.now == pytest.approx(25.0)
    assert max(outage.sleeps) <= 4.1


@pytest.mark.parametrize(
    "failure",
    [
        err(502, "UPSTREAM_ERROR"),
        lambda request: httpx.Response(503, text="Service Unavailable"),
    ],
    ids=["upstream-error", "non-broker-503"],
)
async def test_platform_unavailability_is_retried_for_idempotent_calls(
    failure: Callable[[httpx.Request], httpx.Response],
) -> None:
    outage = Outage(6.0, failure)
    assert await outage.client().request("GET", "/x", operation="x", idempotent=True) == {
        "ok": True
    }
    assert len(outage.requests) > 4  # more than the max_attempts policy allows


async def test_platform_unavailability_is_not_retried_for_non_idempotent_calls() -> None:
    outage = Outage(6.0, err(502, "UPSTREAM_ERROR"))
    with pytest.raises(PlatformError):
        await outage.client().request("POST", "/x", operation="x", idempotent=False, json={})
    assert len(outage.requests) == 1


async def test_provider_unavailability_keeps_the_bounded_retry_policy() -> None:
    outage = Outage(3600.0, err(503, "PROVIDER_UNAVAILABLE"))
    with pytest.raises(ProviderError):
        await outage.client().request("GET", "/x", operation="x", idempotent=True)
    assert len(outage.requests) == 4


@pytest.mark.parametrize(
    ("interval", "budget"), [(10.0, 25.0), (5.0, 12.5), (0.05, 5.0), (600.0, 120.0)]
)
def test_outage_budget_follows_the_heartbeat_interval(interval: float, budget: float) -> None:
    assert outage_budget(interval) == budget


async def test_the_budget_runs_from_the_first_failure_not_from_the_call() -> None:
    """A long poll that had waited 20 s when the broker went away still gets the full budget."""
    outage = Outage(0.0, raise_(httpx.RemoteProtocolError))

    def handler(request: httpx.Request) -> httpx.Response:
        outage.requests.append(request)
        if len(outage.requests) == 1:
            outage.now += 20.0  # the poll waited, then the connection dropped
            raise httpx.RemoteProtocolError("server disconnected", request=request)
        if outage.now < 26.0:  # the broker is back 6 s later
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"state": "answered"})

    outage.handler = handler  # type: ignore[method-assign]
    body = await outage.client().request("GET", "/input", operation="x", idempotent=True)
    assert body == {"state": "answered"}
