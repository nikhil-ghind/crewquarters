import json
from typing import Any

import httpx
import pytest
from fakebroker import FakeBroker
from pydantic import BaseModel

from crewquarters._transport import BrokerClient
from crewquarters.errors import InvalidInput, ModelUnavailable, PermissionDenied
from crewquarters.llm import LLMClient


def response(**overrides: Any) -> dict[str, Any]:
    return {
        "text": "hello",
        "structured": None,
        "finishReason": "stop",
        "usage": {"inputTokens": 3, "outputTokens": 1},
        "provider": "mock-local",
        "model": "mock-small",
        "locality": "local",
        "latencyMs": 5,
        "requestId": "req-1",
        **overrides,
    }


def client(broker: FakeBroker, profiles: tuple[str, ...]) -> LLMClient:
    return LLMClient(BrokerClient("http://broker.test", "t", http=broker.client()), profiles)


def capture(broker: FakeBroker, body: dict[str, Any]) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    broker.overrides[("POST", "/llm/chat")] = handler
    return seen


async def test_family_resolves_to_the_single_granted_variant() -> None:
    broker = FakeBroker()
    seen = capture(broker, response())
    result = await client(broker, ("local.general.small", "cloud.openai.gpt-small")).chat(
        "local.general", [{"role": "user", "content": "hi"}], temperature=0, max_output_tokens=50
    )
    assert seen[0] == {
        "profile": "local.general.small",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0,
        "maxOutputTokens": 50,
        "tools": [],
    }
    assert (result.text, result.locality, result.provider, result.request_id) == (
        "hello",
        "local",
        "mock-local",
        "req-1",
    )
    assert result.usage.input_tokens == 3


def test_resolution_errors() -> None:
    llm = client(FakeBroker(), ("local.general.small", "local.general.quality"))
    with pytest.raises(PermissionDenied, match="ambiguous"):
        llm.resolve_profile("local.general")
    with pytest.raises(PermissionDenied):
        llm.resolve_profile("local.embedding")
    assert llm.resolve_profile("local.general.quality") == "local.general.quality"


def test_local_family_never_resolves_to_cloud() -> None:
    llm = client(FakeBroker(), ("cloud.openai.gpt-small",))
    with pytest.raises(PermissionDenied):
        llm.resolve_profile("local.general")
    with pytest.raises(PermissionDenied):
        llm.resolve_profile("cloud.anthropic.claude-small")
    assert llm.resolve_profile("cloud.openai.gpt-small") == "cloud.openai.gpt-small"


class Answer(BaseModel):
    city: str


async def test_response_model_sets_schema_and_parses() -> None:
    broker = FakeBroker()
    seen = capture(broker, response(text='{"city": "Paris"}', structured={"city": "Paris"}))
    result = await client(broker, ("local.general.small",)).chat(
        "local.general.small", [{"role": "user", "content": "q"}], response_model=Answer, idempotency_key="k1"
    )
    assert result.parsed == Answer(city="Paris")
    assert seen[0]["responseSchema"]["title"] == "Answer"
    assert seen[0]["idempotencyKey"] == "k1"


async def test_response_model_parses_text_when_structured_missing() -> None:
    broker = FakeBroker()
    capture(broker, response(text='{"city": "Rome"}'))
    result = await client(broker, ("local.general.small",)).chat(
        "local.general.small", [{"role": "user", "content": "q"}], response_model=Answer
    )
    assert result.parsed == Answer(city="Rome")


async def test_invalid_structured_output_raises_with_raw_text() -> None:
    broker = FakeBroker()
    capture(broker, response(text="not json at all"))
    with pytest.raises(InvalidInput) as info:
        await client(broker, ("local.general.small",)).chat(
            "local.general.small", [{"role": "user", "content": "q"}], response_model=Answer
        )
    assert info.value.code == "STRUCTURED_OUTPUT_INVALID"
    assert info.value.details["raw"] == "not json at all"


async def test_chat_is_retried_only_with_an_idempotency_key() -> None:
    attempts = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": {"code": "PROVIDER_UNAVAILABLE", "message": "x"}})
        return httpx.Response(200, json=response())

    broker = FakeBroker()
    broker.overrides[("POST", "/llm/chat")] = flaky
    http = broker.client()

    async def no_sleep(_: float) -> None:
        return None

    llm = LLMClient(
        BrokerClient("http://broker.test", "t", http=http, sleep=no_sleep), ("local.general.small",)
    )
    await llm.chat("local.general.small", [{"role": "user", "content": "q"}], idempotency_key="k")
    assert attempts == 2


async def test_model_unavailable_propagates() -> None:
    broker = FakeBroker()
    broker.overrides[("POST", "/llm/chat")] = lambda r: httpx.Response(
        503, json={"error": {"code": "MODEL_UNAVAILABLE", "message": "crashed"}}
    )
    with pytest.raises(ModelUnavailable):
        await client(broker, ("local.general.small",)).chat(
            "local.general.small", [{"role": "user", "content": "q"}]
        )


async def test_invalid_role_is_rejected_locally() -> None:
    llm = client(FakeBroker(), ("local.general.small",))
    with pytest.raises(InvalidInput):
        await llm.chat("local.general.small", [{"role": "tool", "content": "x"}])


async def test_stream_yields_deltas_and_sets_result() -> None:
    body = (
        'event: delta\ndata: {"text": "Hel"}\n\n'
        'event: delta\ndata: {"text": "lo"}\n\n'
        f"event: done\ndata: {json.dumps(response(text='Hello'))}\n\n"
    )
    broker = FakeBroker()
    broker.overrides[("POST", "/llm/chat:stream")] = lambda r: httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )
    stream = client(broker, ("local.general.small",)).stream(
        "local.general", [{"role": "user", "content": "q"}]
    )
    assert [delta async for delta in stream] == ["Hel", "lo"]
    assert stream.result is not None and stream.result.text == "Hello"


async def test_stream_error_event_raises() -> None:
    body = 'event: error\ndata: {"error": {"code": "MODEL_UNAVAILABLE", "message": "down"}}\n\n'
    broker = FakeBroker()
    broker.overrides[("POST", "/llm/chat:stream")] = lambda r: httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )
    stream = client(broker, ("local.general.small",)).stream(
        "local.general.small", [{"role": "user", "content": "q"}]
    )
    with pytest.raises(ModelUnavailable):
        async for _ in stream:
            pass
