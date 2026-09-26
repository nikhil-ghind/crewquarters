"""The fake platform applies the real platform's rules for agents starting agents."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fake_helpers import BASE_MANIFEST

from crewquarters_fake import services
from crewquarters_fake.app import create_app
from crewquarters_fake.errors import ApiError
from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.store import Run, Store


def build(
    agent_id: str, *, triggers: list[str] | None = None, starts: list[str] | None = None
) -> dict[str, Any]:
    manifest = copy.deepcopy(BASE_MANIFEST)
    manifest["metadata"]["id"] = agent_id
    manifest["spec"]["triggers"] = triggers or ["manual", "agent"]
    manifest["spec"]["permissions"].update(
        {"llmProfiles": [], "userInput": False, "startsAgents": starts or []}
    )
    manifest["spec"]["configurationSchema"] = {"type": "object", "properties": {}}
    return manifest


def store_with(**settings: Any) -> Store:
    store: Store = create_app(FakeSettings(**settings)).state.store
    return store


def install(
    store: Store, agent_id: str, config: dict[str, Any] | None = None, **kwargs: Any
) -> str:
    manifest = build(agent_id, **kwargs)
    services.import_manifest(store, manifest, allow_unbuilt=True)
    installation = services.install(
        store, agent_id, None, config or {}, manifest["spec"]["permissions"], None
    )
    return installation.id


def running(store: Store, installation_id: str, **kwargs: Any) -> Run:
    run = services.create_run(store, installation_id, "manual", None, None, **kwargs)
    run.state = "RUNNING"
    return run


def start(store: Store, run: Run, agent_id: str, key: str = "k", **kwargs: Any) -> Any:
    return services.start_agent_run(
        store, run, agent_id=agent_id, start_key=key, trigger_input=kwargs.get("input")
    )


def code(store: Store, run: Run, agent_id: str, key: str = "k", **kwargs: Any) -> str:
    with pytest.raises(ApiError) as caught:
        start(store, run, agent_id, key, **kwargs)
    return caught.value.code


def test_start_creates_a_queued_child_and_a_repeat_returns_it() -> None:
    store = store_with()
    starter = install(store, "starter", starts=["worker"])
    worker = install(store, "worker")
    parent = running(store, starter)
    child, created = start(store, parent, "worker", "a", input={"n": 1})
    assert created and child.state == "QUEUED" and child.installation_id == worker
    assert (child.trigger, child.parent_run_id, child.trigger_input) == (
        "agent",
        parent.id,
        {"n": 1},
    )
    again, created = start(store, parent, "worker", "a")
    assert again.id == child.id and not created
    # The same key cannot switch targets; that check comes before every other rule.
    assert code(store, parent, "starter", "a") == "START_KEY_REUSED"


def test_refusals() -> None:
    store = store_with()
    starter = install(store, "starter", starts=["worker", "manual-only", "ghost"])
    install(store, "worker")
    install(store, "manual-only", triggers=["manual"])
    parent = running(store, starter)
    assert code(store, parent, "stranger") == "PERMISSION_DENIED"
    assert code(store, parent, "ghost") == "TARGET_NOT_INSTALLED"
    assert code(store, parent, "manual-only") == "TRIGGER_NOT_SUPPORTED"
    assert code(store, parent, "worker", input={"x": "y" * 20_000}) == "INPUT_TOO_LARGE"


def test_kill_switch_count_limit_and_cycles() -> None:
    off = store_with(agent_starts_enabled=False)
    starter = install(off, "starter", starts=["worker"])
    install(off, "worker")
    assert code(off, running(off, starter), "worker") == "AGENT_STARTS_DISABLED"

    limited = store_with(agent_starts_per_run=1)
    starter = install(limited, "starter", starts=["worker"])
    install(limited, "worker")
    parent = running(limited, starter)
    start(limited, parent, "worker", "one")
    assert code(limited, parent, "worker", "two") == "START_LIMIT_REACHED"

    chained = store_with(agent_chain_max_depth=2)
    a = install(chained, "agent-a", starts=["agent-b"])
    b = install(chained, "agent-b", starts=["agent-a", "agent-c"])
    c = install(chained, "agent-c", starts=["agent-d"])
    install(chained, "agent-d")
    run_a = running(chained, a)
    run_b, _ = start(chained, run_a, "agent-b")
    run_b.state = "RUNNING"
    assert code(chained, run_b, "agent-a") == "CHAIN_CYCLE"
    run_c, _ = start(chained, run_b, "agent-c")
    run_c.state = "RUNNING"
    assert code(chained, run_c, "agent-d") == "CHAIN_TOO_DEEP"
    assert b and c


def test_two_installations_need_agent_targets() -> None:
    store = store_with()
    first = install(store, "worker")
    second = services.install(
        store, "worker", None, {}, build("worker")["spec"]["permissions"], None
    ).id
    plain = install(store, "starter", starts=["worker"])
    assert code(store, running(store, plain), "worker") == "NEEDS_CONFIGURATION"
    picky = install(store, "starter", {"agentTargets": {"worker": second}}, starts=["worker"])
    child, _ = start(store, running(store, picky), "worker")
    assert child.installation_id == second != first
