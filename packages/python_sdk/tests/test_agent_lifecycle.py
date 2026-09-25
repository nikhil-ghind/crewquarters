import asyncio
import json
import socket
from datetime import UTC, datetime
from typing import Any

import pytest
from fakebroker import FakeBroker
from pydantic import BaseModel

from crewquarters import PROTOCOL, Agent, RunContext, __version__
from crewquarters.errors import AgentError, Cancelled


async def no_sleep(_: float) -> None:
    return None


def assert_cancelled(broker: FakeBroker) -> None:
    [result] = broker.results
    assert result["status"] == "failed"
    assert (result["error"]["code"], result["error"]["retryable"]) == ("RUN_CANCELLED", False)


async def execute(agent: Agent, broker: FakeBroker) -> int:
    return await agent.execute(
        broker_url="http://broker.test", token="t", run_id="run-1", http=broker.client()
    )


async def test_success_posts_succeeded_and_exits_zero() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> dict[str, Any]:
        assert ctx.run.id == "run-1"
        assert ctx.run.attempt == 1
        return {"answer": 42}

    broker = FakeBroker()
    assert await execute(agent, broker) == 0
    assert broker.results == [{"status": "succeeded", "result": {"answer": 42}}]
    handshake = json.loads(broker.requests[0].content)
    assert handshake == {"protocol": PROTOCOL, "sdkVersion": __version__, "agentId": "test-agent"}


async def test_exception_posts_failed_with_redacted_message() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        raise RuntimeError("could not reach +14155550123")

    broker = FakeBroker()
    assert await execute(agent, broker) == 1
    [result] = broker.results
    assert result["status"] == "failed"
    assert result["error"]["code"] == "AGENT_ERROR"
    assert "+14155550123" not in result["error"]["message"]
    assert "••••0123" in result["error"]["message"]


async def test_agent_error_code_and_retryable_are_reported() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        raise AgentError("reconnect google", code="GOOGLE_RECONNECT_REQUIRED", retryable=True)

    broker = FakeBroker()
    await execute(agent, broker)
    error = broker.results[0]["error"]
    assert (error["code"], error["retryable"]) == ("GOOGLE_RECONNECT_REQUIRED", True)


class Config(BaseModel):
    timezone: str
    max_messages: int = 10


async def test_invalid_config_posts_config_invalid_without_running() -> None:
    agent = Agent("test-agent", config_model=Config)
    called = False

    @agent.run
    async def run(ctx: RunContext[Config]) -> None:
        nonlocal called
        called = True

    broker = FakeBroker(config={"max_messages": "many"})
    assert await execute(agent, broker) == 1
    assert not called
    assert broker.results[0]["error"]["code"] == "CONFIG_INVALID"


async def test_valid_config_is_parsed_into_the_model() -> None:
    agent = Agent("test-agent", config_model=Config)
    seen: list[Config] = []

    @agent.run
    async def run(ctx: RunContext[Config]) -> None:
        seen.append(ctx.config)

    await execute(agent, FakeBroker(config={"timezone": "UTC"}))
    assert seen == [Config(timezone="UTC")]


async def test_heartbeat_cancel_request_cancels_the_run() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        await asyncio.sleep(5)

    broker = FakeBroker(heartbeat_interval=0.01)
    broker.cancel_on_heartbeat = 1
    assert await execute(agent, broker) == 1
    assert_cancelled(broker)


async def test_agent_raising_cancelled_posts_cancelled() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        raise Cancelled("broker said stop")

    broker = FakeBroker()
    assert await execute(agent, broker) == 1
    assert_cancelled(broker)


async def test_dict_result_with_datetime_is_serialized() -> None:
    agent = Agent("test-agent")
    when = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)

    @agent.run
    async def run(ctx: RunContext[Any]) -> dict[str, Any]:
        return {"at": when, "items": ({"n": 1},)}

    broker = FakeBroker()
    assert await execute(agent, broker) == 0
    assert broker.results[0]["result"] == {"at": "2026-09-24T10:00:00Z", "items": [{"n": 1}]}


class Result(BaseModel):
    total_count: int


async def test_result_model_is_validated_and_dumped() -> None:
    agent = Agent("test-agent", result_model=Result)

    @agent.run
    async def run(ctx: RunContext[Any]) -> Result:
        return Result(total_count=3)

    broker = FakeBroker()
    assert await execute(agent, broker) == 0
    assert broker.results[0]["result"] == {"total_count": 3}


async def test_invalid_result_fails_the_run() -> None:
    agent = Agent("test-agent", result_model=Result)

    @agent.run
    async def run(ctx: RunContext[Any]) -> dict[str, Any]:
        return {"total_count": "lots"}

    broker = FakeBroker()
    assert await execute(agent, broker) == 1
    assert broker.results[0]["error"]["code"] == "RESULT_INVALID"


async def test_events_are_flushed_before_the_result() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        for i in range(120):
            await ctx.events.log("info", f"line {i}")
        await ctx.events.progress(100, "done")

    broker = FakeBroker(heartbeat_interval=60)
    await execute(agent, broker)
    assert len(broker.events) == 121
    assert max(broker.event_batches) <= 50
    assert len({e["clientEventId"] for e in broker.events}) == 121
    event_paths = [r.url.path for r in broker.requests]
    assert event_paths.index("/internal/v1/sdk/result") > max(
        i for i, p in enumerate(event_paths) if p.endswith("/events")
    )


def test_self_check_prints_versions_without_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("PLATFORM_BROKER_URL", "PLATFORM_RUN_TOKEN", "PLATFORM_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    agent = Agent("self-check-agent")
    with pytest.raises(SystemExit) as info:
        agent.serve(["--self-check"])
    assert info.value.code == 0
    assert json.loads(capsys.readouterr().out) == {
        "sdk": __version__,
        "protocol": PROTOCOL,
        "agent": "self-check-agent",
    }


def test_missing_environment_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("PLATFORM_BROKER_URL", "PLATFORM_RUN_TOKEN", "PLATFORM_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        return None

    with pytest.raises(SystemExit) as info:
        agent.serve([])
    assert info.value.code == 2
    assert "PLATFORM_BROKER_URL" in capsys.readouterr().err


def test_unreachable_broker_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("PLATFORM_BROKER_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("PLATFORM_RUN_TOKEN", "t")
    monkeypatch.setenv("PLATFORM_RUN_ID", "run-1")
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        return None

    with pytest.raises(SystemExit) as info:
        agent.serve([])
    assert info.value.code == 2
    err = capsys.readouterr().err.strip()
    assert "Traceback" not in err
    assert len(err.splitlines()) == 1
    assert "handshake" in err


async def test_event_delivery_failure_never_blocks_the_outcome() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> dict[str, Any]:
        await ctx.events.log("info", "hello")
        return {"ok": True}

    def explode(request: Any) -> Any:
        raise RuntimeError("unexpected failure inside event delivery")

    broker = FakeBroker(heartbeat_interval=60)
    broker.overrides[("POST", "/events")] = explode
    assert await execute(agent, broker) == 0
    assert broker.results == [{"status": "succeeded", "result": {"ok": True}}]


async def test_datetime_log_field_does_not_break_a_successful_run() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> dict[str, Any]:
        await ctx.events.log("info", "started", when=datetime(2026, 9, 24, tzinfo=UTC))
        return {"ok": True}

    broker = FakeBroker(heartbeat_interval=60)
    assert await execute(agent, broker) == 0
    assert broker.events[0]["payload"]["fields"] == {"when": "2026-09-24T00:00:00Z"}


async def test_non_object_result_fails_the_run() -> None:
    agent = Agent("test-agent")

    @agent.run
    async def run(ctx: RunContext[Any]) -> list[int]:
        return [1, 2, 3]

    broker = FakeBroker()
    assert await execute(agent, broker) == 1
    assert broker.results[0]["error"]["code"] == "RESULT_INVALID"


async def test_the_outage_budget_follows_the_handshake_heartbeat_interval() -> None:
    """With a 10 s interval (the platform's 30 s heartbeat timeout) the SDK rides out 25 s of
    broker outage; the heartbeat lease would lapse soon after."""
    agent = Agent("test-agent")
    seen: list[float] = []

    @agent.run
    async def run(ctx: RunContext[Any]) -> None:
        seen.append(ctx.idempotency._transport.outage_budget)

    broker = FakeBroker(heartbeat_interval=10.0)
    assert await execute(agent, broker) == 0
    assert seen == [25.0]
