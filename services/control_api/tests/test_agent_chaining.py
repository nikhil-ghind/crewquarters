"""Agents starting agents through the real control API: approval, the platform's limits, the
ownership and readiness of the target, idempotency and refusals (docs/agent-chaining.md)."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest

from crewquarters_api.catalog import upsert_manifest

SERVICE = {"Authorization": "Bearer dev-insecure-internal-token-change-me-0000"}


def manifest(
    agent_id: str, *, triggers: tuple[str, ...] = ("manual", "agent"), starts: tuple[str, ...] = ()
) -> dict[str, Any]:
    digest = hashlib.sha256(agent_id.encode()).hexdigest()
    return {
        "apiVersion": "crewquarters/v1alpha1",
        "kind": "Agent",
        "metadata": {
            "id": agent_id,
            "name": agent_id.title(),
            "version": "0.1.0",
            "summary": "Chaining test agent.",
            "publisher": "test",
        },
        "spec": {
            "image": f"ghcr.io/crewquarters/{agent_id}@sha256:{digest}",
            "entrypoint": ["python", "-m", "agent"],
            "architectures": ["linux/amd64", "linux/arm64"],
            "triggers": list(triggers),
            "permissions": {
                "llmProfiles": [],
                "knowledge": [],
                "connectors": {},
                "cloudProviders": [],
                "startsAgents": list(starts),
                "userInput": False,
            },
            "resources": {
                "cpu": 0.5,
                "memoryMb": 256,
                "activeTimeoutSeconds": 600,
                "maxInputWaitSeconds": 0,
            },
            "configurationSchema": {"type": "object", "properties": {}},
        },
    }


class Platform:
    def __init__(self, owner: httpx.AsyncClient, sessions: Any, settings: Any, app: Any) -> None:
        from crewquarters_scheduler.worker import Worker

        class InertRuntime:
            async def start_run(self, spec: Any) -> str:
                return f"inert-{spec.run_id}-{spec.attempt}"

        self.owner, self.sessions, self.app = owner, sessions, app
        self.worker = Worker(sessions, InertRuntime(), settings, worker_id="t")  # type: ignore[arg-type]
        self.manifests: dict[str, dict[str, Any]] = {}

    async def install(
        self, agent_id: str, config: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        doc = self.manifests.setdefault(agent_id, manifest(agent_id, **kwargs))
        async with self.sessions() as db:
            await upsert_manifest(db, doc, source="bundled")
            await db.commit()
        response = await self.owner.post(
            "/api/v1/agent-installations",
            json={
                "agentId": agent_id,
                "config": config or {},
                "approvedPermissions": doc["spec"]["permissions"],
            },
        )
        assert response.status_code == 201, response.text
        return dict(response.json())

    async def running(self, installation_id: str) -> str:
        """A manual run that has been dispatched and has said hello, so the test acts as its SDK."""
        run = await self.owner.post("/api/v1/runs", json={"installationId": installation_id})
        assert run.status_code == 201, run.text
        return await self._start(run.json()["id"])

    async def _start(self, run_id: str) -> str:
        assert await self.worker.run_once()
        hello = await self.owner.post(
            f"/internal/v1/runs/{run_id}/handshake", json={"attempt": 1}, headers=SERVICE
        )
        assert hello.status_code == 200, hello.text
        return str(run_id)

    async def start(
        self,
        run_id: str,
        agent_id: str,
        key: str = "k",
        input: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> httpx.Response:
        return await self.owner.post(
            f"/internal/v1/runs/{run_id}/agent-runs",
            json={"attempt": attempt, "agentId": agent_id, "startKey": key, "input": input},
            headers=SERVICE,
        )

    def limits(self, **changes: Any) -> None:
        state = self.app.state.cq
        state.settings = state.settings.model_copy(update=changes)


@pytest.fixture
async def platform(owner: httpx.AsyncClient, sessions: Any, settings: Any, app: Any) -> Platform:
    return Platform(owner, sessions, settings, app)


async def test_an_approved_target_is_started_with_its_own_trigger_parent_and_input(
    platform: Platform,
) -> None:
    starter = await platform.install("starter", starts=("worker",))
    worker = await platform.install("worker")
    parent = await platform.running(starter["id"])

    started = await platform.start(parent, "worker", "pr-7", {"pr": 7})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["created"] is True and body["state"] == "QUEUED"
    assert body["agentId"] == "worker" and body["installationId"] == worker["id"]

    public = (await platform.owner.get(f"/api/v1/runs/{body['runId']}")).json()
    assert public["trigger"] == "agent" and public["parentRunId"] == parent
    internal = (
        await platform.owner.get(f"/internal/v1/runs/{body['runId']}", headers=SERVICE)
    ).json()
    assert internal["triggerInput"] == {"pr": 7} and internal["parentRunId"] == parent
    # The child runs with its own approved permissions, not the caller's.
    assert internal["permissions"]["startsAgents"] == []


async def test_the_same_key_returns_the_same_run_and_cannot_switch_targets(
    platform: Platform,
) -> None:
    starter = await platform.install("starter", starts=("worker", "other"))
    await platform.install("worker")
    await platform.install("other")
    parent = await platform.running(starter["id"])

    first = (await platform.start(parent, "worker", "same")).json()
    again = (await platform.start(parent, "worker", "same")).json()
    assert again["runId"] == first["runId"] and again["created"] is False
    other_key = (await platform.start(parent, "worker", "different")).json()
    assert other_key["runId"] != first["runId"] and other_key["created"] is True
    reused = await platform.start(parent, "other", "same")
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "START_KEY_REUSED"


async def test_only_targets_the_owner_approved_can_be_started(platform: Platform) -> None:
    starter = await platform.install("starter", starts=("worker",))
    await platform.install("worker")
    await platform.install("stranger")
    parent = await platform.running(starter["id"])
    refused = await platform.start(parent, "stranger")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_the_target_must_be_installed_accept_the_trigger_and_be_ready(
    platform: Platform,
) -> None:
    starter = await platform.install("starter", starts=("ghost", "manual-only", "worker"))
    await platform.install("manual-only", triggers=("manual",))
    worker = await platform.install("worker")
    parent = await platform.running(starter["id"])

    ghost = await platform.start(parent, "ghost")
    assert ghost.status_code == 409 and ghost.json()["error"]["code"] == "TARGET_NOT_INSTALLED"
    manual = await platform.start(parent, "manual-only")
    assert manual.json()["error"]["code"] == "TRIGGER_NOT_SUPPORTED"

    off = await platform.owner.patch(
        f"/api/v1/agent-installations/{worker['id']}", json={"version": 1, "enabled": False}
    )
    assert off.status_code == 200, off.text
    disabled = await platform.start(parent, "worker")
    assert disabled.json()["error"]["code"] == "TARGET_NOT_INSTALLED"


async def test_two_installations_need_the_owner_to_pick_one(platform: Platform) -> None:
    first = await platform.install("worker")
    second = await platform.install("worker")
    starter_plain = await platform.install("starter", starts=("worker",))
    parent = await platform.running(starter_plain["id"])
    unclear = await platform.start(parent, "worker")
    assert unclear.status_code == 409
    assert unclear.json()["error"]["code"] == "NEEDS_CONFIGURATION"
    assert unclear.json()["error"]["details"]["key"] == "agentTargets.worker"

    chosen = await platform.install(
        "starter", {"agentTargets": {"worker": second["id"]}}, starts=("worker",)
    )
    parent = await platform.running(chosen["id"])
    ok = await platform.start(parent, "worker")
    assert ok.status_code == 200 and ok.json()["installationId"] == second["id"] != first["id"]


async def test_a_chain_cannot_loop_back_or_go_too_deep(platform: Platform) -> None:
    a = await platform.install("agent-a", starts=("agent-b",))
    await platform.install("agent-b", starts=("agent-a", "agent-c"))
    await platform.install("agent-c", starts=("agent-d",))
    await platform.install("agent-d")
    parent = await platform.running(a["id"])

    b_run = (await platform.start(parent, "agent-b")).json()["runId"]
    await platform._start(b_run)
    loop = await platform.start(b_run, "agent-a")
    assert loop.status_code == 409 and loop.json()["error"]["code"] == "CHAIN_CYCLE"

    platform.limits(agent_chain_max_depth=2)
    c_run = (await platform.start(b_run, "agent-c")).json()["runId"]
    await platform._start(c_run)
    deep = await platform.start(c_run, "agent-d")
    assert deep.status_code == 409 and deep.json()["error"]["code"] == "CHAIN_TOO_DEEP"


async def test_one_run_can_only_start_so_many_and_the_owner_can_switch_it_all_off(
    platform: Platform,
) -> None:
    starter = await platform.install("starter", starts=("worker",))
    await platform.install("worker")
    parent = await platform.running(starter["id"])

    platform.limits(agent_starts_per_run=1)
    assert (await platform.start(parent, "worker", "one")).status_code == 200
    assert (await platform.start(parent, "worker", "one")).json()["created"] is False  # a replay
    over = await platform.start(parent, "worker", "two")
    assert over.status_code == 409 and over.json()["error"]["code"] == "START_LIMIT_REACHED"

    platform.limits(agent_starts_per_run=5, agent_starts_enabled=False)
    off = await platform.start(parent, "worker", "three")
    assert off.status_code == 403 and off.json()["error"]["code"] == "AGENT_STARTS_DISABLED"


async def test_input_is_bounded_and_the_caller_must_be_a_live_current_attempt(
    platform: Platform,
) -> None:
    starter = await platform.install("starter", starts=("worker",))
    await platform.install("worker")
    parent = await platform.running(starter["id"])
    big = await platform.start(parent, "worker", "big", {"text": "x" * 20_000})
    assert big.status_code == 422 and big.json()["error"]["code"] == "INPUT_TOO_LARGE"
    stale = await platform.start(parent, "worker", "stale", attempt=2)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_ATTEMPT"

    queued = await platform.owner.post("/api/v1/runs", json={"installationId": starter["id"]})
    early = await platform.start(queued.json()["id"], "worker", "early")
    assert early.status_code == 409 and early.json()["error"]["code"] == "INVALID_RUN_STATE"
