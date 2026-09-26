import json

import httpx
import pytest
from fakebroker import FakeBroker, error

from crewquarters._transport import BrokerClient
from crewquarters.agents import AgentsClient
from crewquarters.context import Grants, RunInfo
from crewquarters.errors import PermissionDenied, PlatformError


def client(broker: FakeBroker) -> AgentsClient:
    return AgentsClient(BrokerClient("http://broker.test", "t", http=broker.client()))


CHILD = {
    "runId": "run-2",
    "agentId": "worker",
    "installationId": "inst-2",
    "state": "QUEUED",
    "created": True,
}


async def test_start_sends_the_key_and_input_and_returns_the_child() -> None:
    broker = FakeBroker()
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=CHILD)

    broker.overrides[("POST", "/agents/start")] = handler
    started = await client(broker).start("worker", key="pr-7", input={"pr": 7})
    assert bodies == [{"agentId": "worker", "startKey": "pr-7", "input": {"pr": 7}}]
    assert (started.run_id, started.agent_id, started.state) == ("run-2", "worker", "QUEUED")
    assert started.installation_id == "inst-2" and started.created is True


async def test_a_retry_after_a_lost_reply_is_safe_because_the_key_dedupes() -> None:
    broker = FakeBroker()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"code": "UPSTREAM_ERROR", "message": "x"}})
        return httpx.Response(200, json={**CHILD, "created": False})

    broker.overrides[("POST", "/agents/start")] = handler
    started = await client(broker).start("worker", key="k")
    assert calls == 2 and started.created is False


@pytest.mark.parametrize(
    ("status", "code", "kind"),
    [
        (403, "CAPABILITY_DENIED", PermissionDenied),
        (409, "CHAIN_CYCLE", PlatformError),
        (409, "TARGET_NOT_INSTALLED", PlatformError),
    ],
)
async def test_refusals_raise_typed_errors_with_the_platform_code(
    status: int, code: str, kind: type[PlatformError]
) -> None:
    broker = FakeBroker()
    broker.overrides[("POST", "/agents/start")] = lambda request: error(status, code)
    with pytest.raises(kind) as caught:
        await client(broker).start("worker", key="k")
    assert caught.value.code == code


def test_run_info_and_grants_carry_the_starter_and_the_targets() -> None:
    wire = {
        "id": "r",
        "attempt": 1,
        "trigger": "agent",
        "scheduledFor": None,
        "installationId": "i",
        "agentId": "a",
        "agentVersion": "0.1.0",
        "createdAt": "2026-09-24T10:00:00+00:00",
        "parentRunId": "parent",
        "input": {"pr": 7},
    }
    info = RunInfo.from_wire(wire)
    assert (info.trigger, info.parent_run_id, info.input) == ("agent", "parent", {"pr": 7})
    plain = RunInfo.from_wire({k: v for k, v in wire.items() if k not in ("parentRunId", "input")})
    assert plain.parent_run_id is None and plain.input is None
    assert Grants.from_wire({"startsAgents": ["worker"]}).starts_agents == ("worker",)
    assert Grants.from_wire({}).starts_agents == ()
