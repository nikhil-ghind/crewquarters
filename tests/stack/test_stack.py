"""End-to-end integration: control API + scheduler worker + model gateway + runtime
daemon + real Docker containers.

The capability broker (Nikhil Sajan Khaneja, Person 3) is not built yet, so this test
plays its role where an agent would call the platform: it performs the SDK handshake
through the control API's internal route and forwards the agent's real capability
token (read from the container's environment) to the gateway, exactly as the broker
will.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from conftest import ORIGIN, PASSWORD, issue_bootstrap_token
from crewquarters_api.gateway_client import GatewayClient
from crewquarters_api.main import create_app as create_control_app
from crewquarters_gateway.config import GatewaySettings, GiB
from crewquarters_gateway.control import ControlApiClient
from crewquarters_gateway.main import Gateway
from crewquarters_gateway.main import create_app as create_gateway_app
from crewquarters_runtime.config import DaemonConfig
from crewquarters_runtime.docker_api import DockerClient
from crewquarters_runtime.engine import Engine
from crewquarters_runtime.server import DaemonServer
from crewquarters_scheduler.worker import Worker
from crewquarters_shared.runtime import DaemonRuntimeClient

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "dev-insecure-internal-token-change-me-0000"
SERVICE = {"Authorization": f"Bearer {TOKEN}"}
BUSYBOX = "docker.io/library/busybox@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
SMALL = "local.general.small"

pytestmark = pytest.mark.docker


def _docker_ok() -> bool:
    try:
        return DockerClient(Path("/var/run/docker.sock"), timeout=5).ping()
    except OSError:
        return False


if not _docker_ok():  # pragma: no cover
    pytest.skip("Docker is not available", allow_module_level=True)


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[DaemonConfig]:
    suffix = uuid.uuid4().hex[:6]
    cfg = DaemonConfig(
        socket_path=tmp_path / "runtime.sock",
        token=TOKEN,
        token_file=None,
        data_dir=tmp_path / "data",
        model_profiles_dir=ROOT / "catalog/models/dev",
        agent_network=f"cq-agents-i{suffix}",
        model_network=f"cq-models-i{suffix}",
        disk_reserve_bytes=0,
        isolate_model_network=False,  # the in-process gateway reaches models by IP
    )
    engine = Engine(cfg)
    engine.ensure_networks()
    server = DaemonServer(engine, TOKEN, None, cfg.socket_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield cfg
    finally:
        server.shutdown()
        server.server_close()
        docker = engine.docker
        for kind in ("run", "model"):
            for container in docker.container_list({"io.crewquarters.kind": kind}):
                if cfg.agent_network in json.dumps(container) or cfg.model_network in json.dumps(
                    container
                ):
                    docker.container_remove(container["Names"][0].lstrip("/"))
        for profile in engine.profiles.values():
            docker.container_remove(profile.container_name)
        for net in (cfg.agent_network, cfg.model_network):
            subprocess.run(["docker", "network", "rm", net], capture_output=True, check=False)


class Stack:
    def __init__(
        self,
        control: Any,
        gateway: Gateway,
        owner: httpx.AsyncClient,
        gw_http: httpx.AsyncClient,
        worker: Worker,
        cfg: DaemonConfig,
    ) -> None:
        self.control = control
        self.gateway = gateway
        self.owner = owner
        self.gw_http = gw_http
        self.worker = worker
        self.cfg = cfg


@pytest.fixture
async def stack(daemon: DaemonConfig, settings, sessions) -> AsyncIterator[Stack]:  # type: ignore[no-untyped-def]
    control_app = create_control_app(settings)
    control_transport = httpx.ASGITransport(app=control_app)
    gateway = Gateway(
        settings,
        GatewaySettings(
            catalog_dir=ROOT / "catalog/models/dev",
            runtime="daemon",
            runtime_socket=daemon.socket_path,
            model_addressing="ip",
            system_reserve_bytes=1 * GiB,
            max_serving_bytes=12 * GiB,
            load_safety_margin_bytes=1 * GiB,
            load_poll_seconds=0.2,
            wait_ready_seconds=90,
        ),
        control=ControlApiClient("http://control", TOKEN, transport=control_transport),
    )
    await gateway.sync_catalog()
    gateway_app = create_gateway_app(gateway, background=False)
    gateway_transport = httpx.ASGITransport(app=gateway_app)
    state = control_app.state.cq
    state.models = GatewayClient(
        "http://gateway",
        TOKEN,
        transport=gateway_transport,
        chat_token=settings.chat_client_token.get_secret_value(),
    )
    state.runtime = DaemonRuntimeClient(daemon.socket_path, TOKEN)
    stop = asyncio.Event()
    worker_task = asyncio.create_task(gateway.worker_loop(stop))
    runtime = DaemonRuntimeClient(daemon.socket_path, TOKEN)
    worker = Worker(sessions, runtime, settings, worker_id="integration")
    token = await issue_bootstrap_token(sessions)
    async with (
        httpx.AsyncClient(
            transport=control_transport, base_url=ORIGIN, headers={"Origin": ORIGIN}, timeout=120
        ) as owner,
        httpx.AsyncClient(
            transport=gateway_transport, base_url="http://gateway", headers=SERVICE, timeout=120
        ) as gw_http,
    ):
        boot = await owner.post(
            "/api/v1/bootstrap", json={"token": token, "username": "owner", "password": PASSWORD}
        )
        owner.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        try:
            yield Stack(control_app, gateway, owner, gw_http, worker, daemon)
        finally:
            stop.set()
            await worker_task
            await runtime.close()
            await state.runtime.close()
            await state.models.close()
            await gateway.close()
            await control_app.state.engine.dispose()


async def poll_model(
    owner: httpx.AsyncClient, field: str, wanted: str, within: float = 90
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + within
    while True:
        model = (await owner.get(f"/api/v1/models/{SMALL}")).json()
        if model[field] == wanted:
            return dict(model)
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{field} stuck at {model[field]}: {model}")
        await asyncio.sleep(0.25)


def container(name: str) -> dict[str, Any] | None:
    return DockerClient(Path("/var/run/docker.sock")).container_inspect(name)


async def test_model_lifecycle_and_local_chat_through_real_containers(stack: Stack) -> None:
    owner = stack.owner
    installed = await owner.post(f"/api/v1/models/{SMALL}/install")
    assert installed.status_code == 202, installed.text
    await poll_model(owner, "downloadState", "INSTALLED")

    loaded = await owner.post(f"/api/v1/models/{SMALL}/load")
    assert loaded.status_code == 202, loaded.text
    ready = await poll_model(owner, "memoryState", "READY")
    assert ready["reservedBytes"] == 2 * GiB and ready["readyAt"]
    model_container = container("cq-model-local-general-small")
    assert model_container is not None and model_container["State"]["Status"] == "running"
    memory = (await owner.get("/api/v1/system/memory")).json()
    assert memory["reservedBytes"] == 2 * GiB

    session = (await owner.post("/api/v1/chat/sessions", json={"title": "Integration"})).json()
    blocked = await owner.post(
        f"/api/v1/chat/sessions/{session['id']}/messages", json={"content": "hi"}
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "CHAT_DISABLED"
    enabled = (await owner.post(f"/api/v1/chat/sessions/{session['id']}/enable")).json()
    assert enabled["enabled"] is True and enabled["holdsModelLease"] is True

    reply = await owner.post(
        f"/api/v1/chat/sessions/{session['id']}/messages", json={"content": "hello from the owner"}
    )
    assert reply.status_code == 200
    events = [line for line in reply.text.splitlines() if line.startswith("event: ")]
    assert events[0] == "event: message" and events[-1] == "event: done"
    done = json.loads(reply.text.strip().splitlines()[-1][6:])
    assert done["content"] == "Mock reply to: hello from the owner" and done["status"] == "complete"
    detail = (await owner.get(f"/api/v1/chat/sessions/{session['id']}")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    in_use = await owner.post(f"/api/v1/models/{SMALL}/unload", json={})
    assert in_use.status_code == 409 and in_use.json()["error"]["code"] == "MODEL_IN_USE"
    await owner.post(f"/api/v1/chat/sessions/{session['id']}/disable")
    unloaded = await owner.post(f"/api/v1/models/{SMALL}/unload", json={})
    assert unloaded.status_code == 202, unloaded.text
    await poll_model(owner, "memoryState", "NOT_LOADED")
    assert container("cq-model-local-general-small") is None
    status = (await owner.get("/api/v1/system/status")).json()
    names = {c["name"]: c for c in status["checks"]}
    assert names["runtime daemon"]["status"] == "passed" and "gpu" in names


async def test_agent_run_in_real_container_with_brokered_model_call(stack: Stack) -> None:
    owner = stack.owner
    manifest = yaml.safe_load((ROOT / "catalog/dev/hello-crew.yaml").read_text())
    manifest["metadata"]["id"] = "sleepy-crew"
    manifest["spec"]["image"] = BUSYBOX
    manifest["spec"]["entrypoint"] = ["sleep", "300"]
    assert (
        await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    ).status_code == 201
    await owner.post(f"/api/v1/models/{SMALL}/install")
    await poll_model(owner, "downloadState", "INSTALLED")
    installation = (
        await owner.post(
            "/api/v1/agent-installations",
            json={
                "agentId": "sleepy-crew",
                "config": {},
                "approvedPermissions": manifest["spec"]["permissions"],
            },
        )
    ).json()
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    assert await stack.worker.run_once()  # dispatch -> daemon starts a hardened container

    name = f"cq-run-{run['id']}-1"
    info = container(name)
    assert info is not None and info["State"]["Status"] == "running"
    host = info["HostConfig"]
    assert host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"]
    assert (
        host["NetworkMode"] == stack.cfg.agent_network and info["Config"]["User"] == "65532:65532"
    )
    env = dict(item.split("=", 1) for item in info["Config"]["Env"])
    token = env["PLATFORM_RUN_TOKEN"]

    # --- the broker's job: handshake, then forward the agent's LLM call -------------------
    hs = await owner.post(
        f"/internal/v1/runs/{run['id']}/handshake", json={"attempt": 1}, headers=SERVICE
    )
    assert hs.json()["state"] == "RUNNING"
    answer = await stack.gw_http.post(
        "/internal/v1/llm/chat",
        json={
            "profile": "local.general",
            "messages": [{"role": "user", "content": "summarize yesterday"}],
            "responseSchema": {
                "type": "object",
                "required": ["urgent"],
                "properties": {"urgent": {"type": "array"}},
            },
        },
        headers={"X-Capability-Token": token},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["structured"] == {"urgent": []} and answer.json()["model"] == SMALL
    history = (await owner.get(f"/api/v1/runs/{run['id']}/events/history")).json()
    states = [e["payload"]["to"] for e in history if e["type"] == "run.state_changed"]
    assert states == ["QUEUED", "PREPARING", "RUNNING", "LOADING_MODEL", "RUNNING"]
    lease = (await owner.get(f"/api/v1/models/{SMALL}")).json()["activeLeases"]
    assert [item["holderType"] for item in lease] == ["run"]

    # --- cancel: the worker stops the real container ---------------------------------------
    cancelled = await owner.post(f"/api/v1/runs/{run['id']}/cancel")
    assert cancelled.json()["state"] == "CANCELLING"
    assert await stack.worker.run_once()
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["state"] == "CANCELLED"
    assert container(name) is None

    # --- the run's lease is released once the run is no longer active ----------------------
    report = await stack.gateway.manager.reap(stack.gateway.control.run_is_active)
    assert report["runReleased"] == 1
    assert (await owner.get(f"/api/v1/models/{SMALL}")).json()["activeLeases"] == []
    stale = await stack.gw_http.post(
        "/internal/v1/llm/chat",
        json={"profile": "local.general", "messages": [{"role": "user", "content": "again"}]},
        headers={"X-Capability-Token": token},
    )
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "RUN_NOT_ACTIVE"
    await owner.post(f"/api/v1/models/{SMALL}/unload", json={"force": True})
    await poll_model(owner, "memoryState", "NOT_LOADED")
