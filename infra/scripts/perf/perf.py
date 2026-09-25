# ruff: noqa: S603, S607 - a dev tool that runs docker/uv with fixed argument lists
"""Crewquarters performance harness (PLAN.md section 22) for a laptop or the GB10.

It brings up its own Compose stack (project ``cqperf``, its own host ports, image tags,
data directory, and runtime-daemon networks), measures through the edge proxy exactly as
the browser does, writes a JSON results file, and tears the stack down.

    make perf                                   # build images, run everything, tear down
    uv run python infra/scripts/perf/perf.py up
    uv run python infra/scripts/perf/perf.py measure   # writes tmp/perf/results.json
    uv run python infra/scripts/perf/perf.py report tmp/perf/results.json
    uv run python infra/scripts/perf/perf.py down

``measure`` runs, in order: setup-to-first-run, API latency, run dispatch/container start
with SSE event latency, model cold start/admission/first token/idle unload, knowledge
ingestion, and knowledge query at 10k and 100k chunks (exact search, then with an ad hoc
HNSW index that is dropped again). ``--only`` selects sections.

The stack needs: Docker with Compose, the images ``crewquarters/platform:<tag>`` and
``crewquarters/proxy:<tag>`` (``make perf`` builds them), and network access once to pull
the registry image and, with ``--embedding-mode local``, the pinned embedding model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[3]
PERF_DIR = Path(__file__).resolve().parent
COMPOSE_FILES = [
    ROOT / "infra/compose/compose.yaml",
    ROOT / "infra/compose/compose.runtime.yaml",
    PERF_DIR / "compose.perf.yaml",
]
PASSWORD = "perf-owner-password-0123456789"  # noqa: S105 - throwaway local stack
AGENT = "contract_probe"
AGENT_ID = "contract-probe"
SMALL = "local.general.small"
QUALITY = "local.general.quality"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}


# --- configuration --------------------------------------------------------------------


@dataclass
class Stack:
    project: str = "cqperf"
    http_port: int = 18094
    postgres_port: int = 15446
    registry_port: int = 15021
    tag: str = "perf"
    embedding_mode: str = "local"
    data_dir: Path = Path("/tmp/cqperf-data")  # noqa: S108 - must be the same path in and out
    idle_unload_seconds: int = 60

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.http_port}"

    @property
    def db_url(self) -> str:
        return (
            "postgresql+psycopg://crewquarters:crewquarters@127.0.0.1:"
            f"{self.postgres_port}/crewquarters"
        )

    @property
    def agent_network(self) -> str:
        return f"{self.project}-agents"

    @property
    def model_network(self) -> str:
        return f"{self.project}-models"

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "COMPOSE_PROJECT_NAME": self.project,
                "CQ_HTTP_PORT": str(self.http_port),
                "CQ_POSTGRES_PORT": str(self.postgres_port),
                "CQ_PERF_REGISTRY_PORT": str(self.registry_port),
                "CQ_PLATFORM_IMAGE": f"crewquarters/platform:{self.tag}",
                "CQ_PROXY_IMAGE": f"crewquarters/proxy:{self.tag}",
                "CQ_DATA_DIR": str(self.data_dir),
                "CQ_PUBLIC_BASE_URL": self.base_url,
                "CQ_EMBEDDING_MODE": self.embedding_mode,
                "CQ_PERF_AGENT_NETWORK": self.agent_network,
                "CQ_PERF_MODEL_NETWORK": self.model_network,
                "CQ_PERF_IDLE_UNLOAD_SECONDS": str(self.idle_unload_seconds),
            }
        )
        return env

    def compose(self, *args: str) -> list[str]:
        cmd = ["docker", "compose", "-p", self.project]
        for f in COMPOSE_FILES:
            cmd += ["-f", str(f)]
        return [*cmd, *args]

    def run(self, *args: str, check: bool = True, capture: bool = False) -> str:
        result = subprocess.run(
            self.compose(*args),
            env=self.env(),
            check=check,
            text=True,
            capture_output=capture,
        )
        return result.stdout if capture else ""


def log(message: str) -> None:
    print(f"[perf {time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


# --- statistics -----------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    rank = max(1, min(len(ordered), round(p / 100 * len(ordered) + 0.5)))
    return ordered[rank - 1]


def summarize(values: list[float], digits: int = 1) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(percentile(values, 50), digits),
        "p95": round(percentile(values, 95), digits),
        "p99": round(percentile(values, 99), digits),
        "mean": round(statistics.fmean(values), digits),
        "max": round(max(values), digits),
    }


def parse_time(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


# --- stack lifecycle ------------------------------------------------------------------


def stack_up(stack: Stack) -> dict[str, float]:
    """Start the stack from empty volumes. Returns phase timings in seconds."""
    for image in (f"crewquarters/platform:{stack.tag}", f"crewquarters/proxy:{stack.tag}"):
        found = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, check=False
        )
        if found.returncode != 0:
            sys.exit(f"missing image {image}: run `make perf` or build it with TAG={stack.tag}")
    stack.data_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    log("starting runtime daemon and registry")
    stack.run("up", "-d", "--wait", "runtime-daemon", "perf-registry")
    daemon = time.perf_counter()
    log("starting the core stack")
    stack.run("up", "-d", "--wait")
    done = time.perf_counter()
    return {
        "daemonAndRegistrySeconds": round(daemon - started, 1),
        "coreStackSeconds": round(done - daemon, 1),
        "composeUpSeconds": round(done - started, 1),
    }


def stack_down(stack: Stack) -> None:
    log("removing agent/model containers started by this stack's daemon")
    for network in (stack.agent_network, stack.model_network):
        ids = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"network={network}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        compose_ids = set(stack.run("ps", "-aq", capture=True, check=False).split())
        extra = [i for i in ids if i not in compose_ids]
        if extra:
            subprocess.run(["docker", "rm", "-f", *extra], capture_output=True, check=False)
    stack.run("down", "-v", "--remove-orphans", check=False)
    for network in (stack.agent_network, stack.model_network):
        subprocess.run(["docker", "network", "rm", network], capture_output=True, check=False)
    if stack.data_dir.exists():
        # The daemon writes run/model files as root; remove them from a container.
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{stack.data_dir}:/data",
                f"crewquarters/platform:{stack.tag}",
                "sh",
                "-c",
                "rm -rf /data/* /data/.[!.]*",
            ],
            capture_output=True,
            check=False,
            env=stack.env(),
        )
        shutil.rmtree(stack.data_dir, ignore_errors=True)


def build_agent_image(stack: Stack, out_dir: Path) -> Path:
    """Build the contract-probe agent for this machine, push it to the stack's registry and
    return the digest-pinned manifest."""
    from crewctl.build import host_platform

    manifest = out_dir / f"{AGENT}.manifest.yaml"
    subprocess.run(
        [
            "uv",
            "run",
            "crewctl",
            "build",
            str(ROOT / "agents" / AGENT),
            "--push",
            "--registry",
            f"localhost:{stack.registry_port}",
            "--platform",
            host_platform(),
            "--output-manifest",
            str(manifest),
        ],
        check=True,
        cwd=ROOT,
    )
    return manifest


def bootstrap_token(stack: Stack) -> str:
    out = stack.run("exec", "-T", "control-api", "cq-admin", "bootstrap-token", capture=True)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return lines[-1].split()[-1]


# --- API client -----------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, connections: int = 100) -> None:
        self.base_url = base_url
        self.http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Origin": base_url},
            timeout=httpx.Timeout(120.0),
            limits=httpx.Limits(max_connections=connections, max_keepalive_connections=connections),
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        allow_error = bool(kwargs.pop("allow_error", False))
        response = await self.http.request(method, path, **kwargs)
        if response.status_code >= 400 and not allow_error:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response

    async def json(self, method: str, path: str, **kwargs: Any) -> Any:
        return (await self.call(method, path, **kwargs)).json()

    async def bootstrap(self, token: str) -> None:
        body = {"token": token, "username": "owner", "password": PASSWORD}
        data = await self.json("POST", "/api/v1/bootstrap", json=body)
        self.http.headers["X-CSRF-Token"] = data["csrfToken"]

    async def login(self) -> None:
        body = {"username": "owner", "password": PASSWORD}
        data = await self.json("POST", "/api/v1/sessions", json=body)
        self.http.headers["X-CSRF-Token"] = data["csrfToken"]

    async def wait_run(self, run_id: str, limit_s: float = 300) -> dict[str, Any]:
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            run = await self.json("GET", f"/api/v1/runs/{run_id}")
            if run["state"] in TERMINAL:
                return dict(run)
            await asyncio.sleep(0.25)
        raise TimeoutError(f"run {run_id} did not finish")

    async def model(self, model_id: str) -> dict[str, Any]:
        return dict(await self.json("GET", f"/api/v1/models/{model_id}"))

    async def wait_model(
        self, model_id: str, field_name: str, wanted: set[str], limit_s: float = 600
    ) -> dict[str, Any]:
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            model = await self.model(model_id)
            if model[field_name] in wanted:
                return model
            if model[field_name] in ("LOAD_ERROR", "ERROR", "DOWNLOAD_ERROR"):
                raise RuntimeError(f"{model_id}: {model.get('error')}")
            await asyncio.sleep(0.1)
        raise TimeoutError(f"{model_id} never reached {wanted}")


def load_manifest(path: Path) -> dict[str, Any]:
    import yaml

    return dict(yaml.safe_load(path.read_text()))


async def install_probe(api: Api, manifest: dict[str, Any], kb_id: str) -> str:
    body = {
        "agentId": AGENT_ID,
        "config": {"checks": ["handshake", "events"], "knowledgeBaseId": kb_id},
        "approvedPermissions": manifest["spec"]["permissions"],
    }
    data = await api.json("POST", "/api/v1/agent-installations", json=body)
    return str(data["id"])


# --- sections -------------------------------------------------------------------------


@dataclass
class Context:
    stack: Stack
    api: Api
    manifest: dict[str, Any]
    results: dict[str, Any] = field(default_factory=dict)
    kb_id: str = ""
    installation_id: str = ""


async def section_setup(ctx: Context, compose_timings: dict[str, float] | None) -> None:
    """Owner bootstrap to a first successful real agent run, all through the API."""
    api = ctx.api
    t0 = time.perf_counter()
    token = await asyncio.to_thread(bootstrap_token, ctx.stack)
    await api.bootstrap(token)
    t_boot = time.perf_counter()
    # The agent requests local.general, so readiness needs the small model installed.
    await api.call("POST", f"/api/v1/models/{SMALL}/install")
    await api.wait_model(SMALL, "downloadState", {"INSTALLED"})
    await api.json("POST", "/api/v1/catalog/agents/import", json={"manifest": ctx.manifest})
    kb = await api.json("POST", "/api/v1/knowledge-bases", json={"name": "Perf probe"})
    ctx.kb_id = str(kb["id"])
    ctx.installation_id = await install_probe(api, ctx.manifest, ctx.kb_id)
    t_install = time.perf_counter()
    run = await api.json("POST", "/api/v1/runs", json={"installationId": ctx.installation_id})
    done = await api.wait_run(str(run["id"]))
    t_run = time.perf_counter()
    if done["state"] != "SUCCEEDED":
        raise RuntimeError(f"first run ended {done['state']}: {done.get('error')}")
    api_seconds = t_run - t0
    ctx.results["setup"] = {
        "compose": compose_timings,
        "bootstrapSeconds": round(t_boot - t0, 2),
        "importAndInstallSeconds": round(t_install - t_boot, 2),
        "firstRunSeconds": round(t_run - t_install, 2),
        "firstRunNote": "includes pulling the agent image from the local registry",
        "apiSetupToFirstRunSeconds": round(api_seconds, 2),
        "totalSeconds": round(api_seconds + (compose_timings or {}).get("composeUpSeconds", 0), 1),
    }
    log(f"setup: {ctx.results['setup']}")


async def _load(
    api: Api,
    concurrency: int,
    total: int,
    request: Callable[[int], Awaitable[httpx.Response]],
) -> dict[str, Any]:
    latencies: list[float] = []
    errors = 0
    counter = iter(range(total))

    async def worker() -> None:
        nonlocal errors
        for i in counter:
            started = time.perf_counter()
            response = await request(i)
            latencies.append((time.perf_counter() - started) * 1000)
            if response.status_code >= 400:
                errors += 1

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {**summarize(latencies), "errors": errors, "rps": round(total / elapsed, 1)}


def _getter(api: Api, path: str) -> Callable[[int], Awaitable[httpx.Response]]:
    async def get(_: int) -> httpx.Response:
        return await api.http.get(path)

    return get


async def section_api(ctx: Context, levels: Iterable[int] = (1, 16, 64)) -> None:
    """p50/p95/p99 of key reads and writes through the proxy, per concurrency level."""
    api = ctx.api
    reads = {
        "GET /me": "/api/v1/me",
        "GET /runs?limit=20": "/api/v1/runs?limit=20",
        "GET /runs/{id}": None,
        "GET /agent-installations": "/api/v1/agent-installations",
        "GET /catalog/agents": "/api/v1/catalog/agents",
        "GET /models": "/api/v1/models",
        "GET /knowledge-bases": "/api/v1/knowledge-bases",
        "GET /attention": "/api/v1/attention",
        "GET /system/status": "/api/v1/system/status",
    }
    runs = await api.json("GET", "/api/v1/runs?limit=1")
    run_id = runs["items"][0]["id"]
    reads["GET /runs/{id}"] = f"/api/v1/runs/{run_id}"
    zones = ["UTC", "Asia/Kolkata", "Europe/Berlin", "America/New_York"]
    kb_ids: list[str] = []

    async def create_kb(i: int) -> httpx.Response:
        name = f"perf-{uuid.uuid4().hex[:12]}"
        response = await api.http.post("/api/v1/knowledge-bases", json={"name": name})
        if response.status_code < 400:
            kb_ids.append(response.json()["id"])
        return response

    async def delete_kb(i: int) -> httpx.Response:
        return await api.http.delete(f"/api/v1/knowledge-bases/{kb_ids[i]}")

    writes: dict[str, Callable[[int], Awaitable[httpx.Response]]] = {
        "PATCH /settings": lambda i: api.http.patch(
            "/api/v1/settings", json={"timezone": zones[i % len(zones)]}
        ),
        "POST /schedules/preview": lambda i: api.http.post(
            "/api/v1/schedules/preview",
            json={"cron": "0 10 * * *", "timezone": zones[i % len(zones)], "count": 5},
        ),
        "POST /knowledge-bases": create_kb,
        "DELETE /knowledge-bases/{id}": delete_kb,
    }
    out: dict[str, Any] = {}
    for level in levels:
        total = max(200, level * 10)
        per_level: dict[str, Any] = {}
        for name, path in reads.items():
            assert path is not None
            per_level[name] = await _load(api, level, total, _getter(api, path))
        for name, fn in writes.items():
            if name == "DELETE /knowledge-bases/{id}":
                per_level[name] = await _load(api, level, len(kb_ids), fn)
                kb_ids.clear()
            else:
                per_level[name] = await _load(api, level, total, fn)
        out[f"c{level}"] = per_level
        worst = max(v["p95"] for v in per_level.values())
        log(f"api c={level}: worst p95 {worst} ms")
    # Restore the owner's timezone.
    await api.http.patch("/api/v1/settings", json={"timezone": "UTC"})
    ctx.results["api"] = out


def _container_started(name: str) -> float | None:
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    value = out.stdout.strip()
    if out.returncode != 0 or not value or value.startswith("0001-"):
        return None
    # Docker prints nanoseconds and a Z or offset; Python takes at most microseconds.
    match = re.match(r"^(.*?T\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$", value)
    if match is None:
        return None
    fraction = (match.group(2) or "0")[:6].ljust(6, "0")
    zone = "+00:00" if match.group(3) == "Z" else match.group(3)
    return datetime.fromisoformat(f"{match.group(1)}.{fraction}{zone}").timestamp()


async def _sse_latencies(api: Api, run_id: str, sink: list[float], opened: list[float]) -> None:
    """Receive a run's events and record receipt time minus the event's created_at, for
    events created after the stream opened (older ones are replays, not live delivery)."""
    async with api.http.stream("GET", f"/api/v1/runs/{run_id}/events") as response:
        opened.append(time.time())
        event, data = "", ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
            elif line == "":
                if event == "end":
                    return
                if data and event:
                    received = time.time()
                    created = parse_time(json.loads(data)["createdAt"])
                    if created >= opened[0]:
                        sink.append((received - created) * 1000)
                event, data = "", ""


async def section_dispatch(ctx: Context, runs: int = 10, burst: int = 12) -> None:
    """POST /runs -> PREPARING (dispatch) -> container started -> RUNNING (SDK handshake),
    one run at a time with the image already present, then a concurrent burst. SSE event
    latency is measured on the same runs."""
    api = ctx.api
    rows: list[dict[str, float]] = []
    sse: list[float] = []
    post_ms: list[float] = []
    for _ in range(runs):
        t_post = time.time()
        started = time.perf_counter()
        run = await api.json("POST", "/api/v1/runs", json={"installationId": ctx.installation_id})
        post_ms.append((time.perf_counter() - started) * 1000)
        run_id = str(run["id"])
        opened: list[float] = []
        stream = asyncio.create_task(_sse_latencies(api, run_id, sse, opened))
        container = f"cq-run-{run_id}-1"
        started_at = None
        deadline = time.monotonic() + 60
        while started_at is None and time.monotonic() < deadline:
            started_at = await asyncio.to_thread(_container_started, container)
            if started_at is None:
                await asyncio.sleep(0.05)
        done = await api.wait_run(run_id)
        await asyncio.wait_for(stream, 30)
        history = await api.json("GET", f"/api/v1/runs/{run_id}/events/history?limit=500")
        items = history["items"] if isinstance(history, dict) else history
        states = {
            e["payload"].get("to"): parse_time(e["createdAt"])
            for e in items
            if e["type"] == "run.state_changed"
        }
        created = parse_time(done["createdAt"])
        rows.append(
            {
                "createdToPreparingMs": (states["PREPARING"] - created) * 1000,
                "createdToContainerStartMs": ((started_at or float("nan")) - created) * 1000,
                "createdToRunningMs": (states["RUNNING"] - created) * 1000,
                "postToRunningMs": (states["RUNNING"] - t_post) * 1000,
                "totalRunMs": (states[done["state"]] - created) * 1000,
            }
        )
    keys = rows[0].keys()
    result: dict[str, Any] = {
        key: summarize([r[key] for r in rows if r[key] == r[key]]) for key in keys
    }
    result["postRunsMs"] = summarize(post_ms)
    result["sseEventLatencyMs"] = summarize(sse)

    # Burst: N concurrent POST /runs, then wait until every container has started.
    started = time.perf_counter()

    async def post(_: int) -> dict[str, Any]:
        t = time.perf_counter()
        run = await api.json("POST", "/api/v1/runs", json={"installationId": ctx.installation_id})
        return {"id": run["id"], "ms": (time.perf_counter() - t) * 1000}

    posted = await asyncio.gather(*(post(i) for i in range(burst)))
    finals = await asyncio.gather(*(api.wait_run(str(p["id"]), 600) for p in posted))
    burst_ms = []
    for final in finals:
        history = await api.json("GET", f"/api/v1/runs/{final['id']}/events/history?limit=500")
        items = history["items"] if isinstance(history, dict) else history
        running = [
            parse_time(e["createdAt"])
            for e in items
            if e["type"] == "run.state_changed" and e["payload"].get("to") == "RUNNING"
        ]
        if running:
            burst_ms.append((running[0] - parse_time(final["createdAt"])) * 1000)
    result["burst"] = {
        "runs": burst,
        "postRunsMs": summarize([p["ms"] for p in posted]),
        "createdToRunningMs": summarize(burst_ms),
        "succeeded": sum(1 for f in finals if f["state"] == "SUCCEEDED"),
        "wallSeconds": round(time.perf_counter() - started, 1),
    }
    ctx.results["dispatch"] = result
    log(f"dispatch: {json.dumps({k: v for k, v in result.items() if k != 'burst'})}")


async def section_models(ctx: Context, cold_starts: int = 3, chats: int = 20) -> None:
    """Mock-model cold start (manual load until READY), admission refusal, warm chat first
    token through the proxy, manual unload, and idle unload after the configured grace."""
    api = ctx.api
    busy = (
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", "name=cq-model-"],
            capture_output=True,
            text=True,
            check=False,
        )
    ).stdout.split()
    if busy:
        ctx.results["models"] = {
            "skipped": f"model containers from another stack exist ({', '.join(busy)}); "
            "model container names are fixed, so this section would disturb them"
        }
        log(ctx.results["models"]["skipped"])
        return
    out: dict[str, Any] = {}
    for model_id in (SMALL, QUALITY):
        t = time.perf_counter()
        await api.call("POST", f"/api/v1/models/{model_id}/install", allow_error=True)
        await api.wait_model(model_id, "downloadState", {"INSTALLED"})
        out.setdefault("installSeconds", {})[model_id] = round(time.perf_counter() - t, 2)

    loads, unloads = [], []
    for _ in range(cold_starts):
        t = time.perf_counter()
        await api.call("POST", f"/api/v1/models/{SMALL}/load")
        model = await api.wait_model(SMALL, "memoryState", {"READY"})
        loads.append(time.perf_counter() - t)
        server = None
        if model.get("loadStartedAt") and model.get("readyAt"):
            server = parse_time(model["readyAt"]) - parse_time(model["loadStartedAt"])
        out.setdefault("serverLoadSeconds", []).append(round(server or -1, 2))
        t = time.perf_counter()
        await api.call("POST", f"/api/v1/models/{SMALL}/unload", json={"force": True})
        await api.wait_model(SMALL, "memoryState", {"NOT_LOADED"})
        unloads.append(time.perf_counter() - t)
    out["coldStartSeconds"] = summarize(loads, 2)
    out["manualUnloadSeconds"] = summarize(unloads, 2)

    # Admission: with small resident, a second generative model is refused (one model by
    # default), and the refusal is fast.
    await api.call("POST", f"/api/v1/models/{SMALL}/load")
    await api.wait_model(SMALL, "memoryState", {"READY"})
    refusals = []
    codes = set()
    for _ in range(20):
        t = time.perf_counter()
        response = await api.call("POST", f"/api/v1/models/{QUALITY}/load", allow_error=True)
        refusals.append((time.perf_counter() - t) * 1000)
        codes.add(
            response.json().get("error", {}).get("code")
            if response.status_code >= 400
            else str(response.status_code)
        )
    out["admissionRefusalMs"] = summarize(refusals)
    out["admissionCodes"] = sorted(c for c in codes if c)

    # Warm chat: first streamed token and full reply through the proxy (mock model).
    session = await api.json(
        "POST", "/api/v1/chat/sessions", json={"title": "perf", "modelProfile": SMALL}
    )
    sid = session["id"]
    await api.call("POST", f"/api/v1/chat/sessions/{sid}/enable")
    await api.wait_model(SMALL, "memoryState", {"READY"})
    firsts, fulls = [], []
    for i in range(chats):
        t = time.perf_counter()
        first = None
        async with api.http.stream(
            "POST", f"/api/v1/chat/sessions/{sid}/messages", json={"content": f"hello {i}"}
        ) as response:
            async for line in response.aiter_lines():
                if first is None and line.startswith("event: delta"):
                    first = time.perf_counter() - t
        fulls.append((time.perf_counter() - t) * 1000)
        firsts.append((first or float("nan")) * 1000)
    out["chatFirstTokenMs"] = summarize(firsts)
    out["chatFullReplyMs"] = summarize(fulls)

    # Idle unload: release every lease and time how long the model stays resident.
    await api.call("POST", f"/api/v1/chat/sessions/{sid}/disable")
    released = time.perf_counter()
    grace = ctx.stack.idle_unload_seconds
    model = await api.wait_model(SMALL, "memoryState", {"NOT_LOADED"}, limit_s=grace + 300)
    idle = time.perf_counter() - released
    out["idleUnload"] = {
        "graceSeconds": grace,
        "releasedToUnloadedSeconds": round(idle, 1),
        "lateBySeconds": round(idle - grace, 1),
        "finalState": model["memoryState"],
    }
    ctx.results["models"] = out
    log(f"models: {json.dumps(out)}")


# --- knowledge ------------------------------------------------------------------------

_WORDS_TEXT = (
    "account agent allowance approval archive audit backup balance billing budget calendar "
    "cancellation capacity certificate checklist claim client compliance contract coverage "
    "customer deadline delivery deposit device digest discount dispute document download "
    "email employee escalation estimate expense export feedback filing firmware forecast "
    "guarantee hardware holiday incident inspection installation insurance inventory invoice "
    "laptop lease license maintenance meeting memory migration model network notice "
    "onboarding outage overtime password payment payroll penalty permission policy portal "
    "pricing privacy procurement project refund reimbursement renewal report request "
    "reservation retention review rollback salary schedule security server shipment "
    "signature software storage subscription supplier support survey tax ticket timesheet "
    "training transfer travel update upgrade vacation vendor warranty workflow"
)
_FILLER_TEXT = "the a of to and in for on with is are be by this that it as at from or"
WORDS = _WORDS_TEXT.split()
FILLER = _FILLER_TEXT.split()


def synth_text(rng: random.Random, words: int) -> str:
    out = []
    for i in range(words):
        out.append(rng.choice(WORDS) if rng.random() < 0.45 else rng.choice(FILLER))
        if i % 17 == 16:
            out[-1] += "."
    return " ".join(out).capitalize()


def synth_document(rng: random.Random, sections: int) -> bytes:
    parts = []
    for s in range(sections):
        parts.append(f"# {rng.choice(WORDS).title()} section {s}\n\n{synth_text(rng, 350)}\n")
    return "\n".join(parts).encode()


QUERIES = [
    "What is the refund policy for cancelled subscriptions?",
    "How many vacation days do employees get each year?",
    "Who approves overtime and expense reimbursement?",
    "When is the invoice payment deadline?",
    "How do I reset my portal password?",
    "What does the hardware warranty cover?",
    "How long are backups retained?",
    "What is the escalation path for a security incident?",
    "Which vendor handles shipment delivery?",
    "How is the travel allowance calculated?",
    "What training is required during onboarding?",
    "What happens when a license renewal is late?",
    "How are firmware upgrades scheduled?",
    "Where are signed contracts archived?",
    "What is the maintenance window for the servers?",
    "How do I dispute a billing penalty?",
    "Who reviews procurement requests over budget?",
    "How is payroll affected by unpaid leave?",
    "What privacy review is needed before export?",
    "How do I request a laptop replacement?",
]


async def section_ingest(ctx: Context, documents: int = 12, sections: int = 20) -> None:
    """Upload generated Markdown through the API and time until every document is READY."""
    api = ctx.api
    rng = random.Random(7)  # noqa: S311 - reproducible synthetic text
    kb = await api.json(
        "POST", "/api/v1/knowledge-bases", json={"name": f"Perf ingest {uuid.uuid4().hex[:8]}"}
    )
    kb_id = kb["id"]
    payloads = [synth_document(rng, sections) for _ in range(documents)]
    total_bytes = sum(len(p) for p in payloads)
    started = time.perf_counter()
    doc_ids = []
    for i, payload in enumerate(payloads):
        response = await api.json(
            "POST",
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            files={"file": (f"handbook-{i}.md", payload, "text/markdown")},
        )
        doc_ids.append(response["id"])
    uploaded = time.perf_counter()
    chunks = 0
    while True:
        docs = await api.json("GET", f"/api/v1/knowledge-bases/{kb_id}/documents?limit=100")
        items = docs["items"]
        states = [d["state"] for d in items]
        if any(s == "FAILED" for s in states):
            raise RuntimeError(f"ingestion failed: {[d.get('error') for d in items]}")
        if len(items) == documents and all(s == "READY" for s in states):
            chunks = sum(int((d.get("extracted") or {}).get("chunks") or 0) for d in items)
            if not chunks:
                chunks = sum(int(d.get("chunkCount") or d.get("chunks") or 0) for d in items)
            break
        await asyncio.sleep(0.2)
    elapsed = time.perf_counter() - started
    ctx.results["ingest"] = {
        "embeddingProfile": kb.get("embeddingProfile"),
        "documents": documents,
        "bytes": total_bytes,
        "chunks": chunks,
        "uploadSeconds": round(uploaded - started, 2),
        "uploadToAllReadySeconds": round(elapsed, 2),
        "chunksPerSecond": round(chunks / elapsed, 1) if chunks else None,
        "mibPerMinute": round(total_bytes / 1024**2 / elapsed * 60, 2),
    }
    log(f"ingest: {ctx.results['ingest']}")


def _vectors(rng: Any, n: int, centroids: Any, mean: Any) -> Any:
    import numpy as np

    picks = rng.integers(0, len(centroids), n)
    vecs = mean + centroids[picks] + rng.normal(0, 0.35, (n, mean.shape[0])) / 8
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs.astype(np.float32)


class Seeder:
    """Writes synthetic chunks with the knowledge service's own table models (512-dim
    vectors), in documents of 100 chunks marked READY, into one knowledge base."""

    def __init__(self, db_url: str, kb_id: str) -> None:
        import numpy as np
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url)
        self.kb_id = uuid.UUID(kb_id)
        self.rng = np.random.default_rng(11)
        dim = 512
        self.mean = self.rng.normal(0, 1, dim)
        self.mean /= np.linalg.norm(self.mean)
        self.centroids = self.rng.normal(0, 1, (256, dim)) / np.sqrt(dim) * 1.6
        self.text_rng = random.Random(3)  # noqa: S311
        self.count = 0

    def sample_vectors(self, n: int) -> Any:
        return _vectors(self.rng, n, self.centroids, self.mean)

    def seed(self, target: int, per_doc: int = 100) -> float:
        from sqlalchemy import insert

        from crewquarters_knowledge.models import Document, DocumentChunk
        from crewquarters_shared.ids import uuid7

        started = time.perf_counter()
        texts = [synth_text(self.text_rng, 420) for _ in range(500)]
        while self.count < target:
            n = min(per_doc * 20, target - self.count)
            docs, chunks = [], []
            vecs = self.sample_vectors(n)
            for d in range(0, n, per_doc):
                doc_id = uuid7()
                docs.append(
                    {
                        "id": doc_id,
                        "kb_id": self.kb_id,
                        "name": f"seed-{self.count + d:07d}.md",
                        "mime": "text/markdown",
                        "path": f"seed/{doc_id}",
                        "sha256": uuid.uuid4().hex + uuid.uuid4().hex,
                        "bytes": per_doc * 2800,
                        "state": "READY",
                        "extracted": {"chunks": per_doc, "seeded": True},
                    }
                )
                for o in range(min(per_doc, n - d)):
                    i = d + o
                    chunks.append(
                        {
                            "id": uuid7(),
                            "document_id": doc_id,
                            "ordinal": o,
                            "text": texts[(self.count + i) % len(texts)],
                            "token_count": 480,
                            "locator": {"section": f"Section {o}", "line": o * 12 + 1},
                            "embedding": vecs[i],
                        }
                    )
            with self.engine.begin() as conn:
                conn.execute(insert(Document), docs)
                conn.execute(insert(DocumentChunk), chunks)
            self.count += n
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ANALYZE document_chunks")
        return time.perf_counter() - started

    def sql_query(self, vector: Any, top_k: int = 8) -> tuple[list[str], float]:
        """The knowledge service's query statement (service.query), timed in the database
        client, without the embedding step."""
        from sqlalchemy import select

        from crewquarters_knowledge.models import Document, DocumentChunk

        distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")
        stmt = (
            select(DocumentChunk.id, Document.name, distance)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.kb_id == self.kb_id, Document.state == "READY")
            .order_by(distance)
            .limit(top_k)
        )
        with self.engine.connect() as conn:
            started = time.perf_counter()
            rows = conn.execute(stmt).all()
            return [str(r[0]) for r in rows], (time.perf_counter() - started) * 1000

    def explain(self, vector: Any) -> str:
        """EXPLAIN ANALYZE of the same statement (written out in SQL)."""
        from sqlalchemy import text

        literal = "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"
        sql = text(
            "EXPLAIN (ANALYZE, TIMING OFF) "
            "SELECT c.id, c.embedding <=> CAST(:v AS vector) AS distance "
            "FROM document_chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.kb_id = :kb AND d.state = 'READY' ORDER BY distance LIMIT 8"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"v": literal, "kb": self.kb_id}).all()
        # Drop the 512-number vector literal from the plan text.
        return "\n".join(re.sub(r"'\[[^\]]*\]'", "'[...]'", str(r[0])) for r in rows)

    def create_hnsw(self) -> float:
        started = time.perf_counter()
        with self.engine.begin() as conn:
            conn.exec_driver_sql("SET maintenance_work_mem = '1GB'")
            conn.exec_driver_sql(
                "CREATE INDEX perf_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        return time.perf_counter() - started

    def drop_hnsw(self) -> None:
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DROP INDEX IF EXISTS perf_hnsw")

    def table_size(self) -> str:
        with self.engine.connect() as conn:
            value = conn.exec_driver_sql(
                "SELECT pg_size_pretty(pg_total_relation_size('document_chunks'))"
            ).scalar()
        return str(value)


async def _api_queries(api: Api, kb_id: str, rounds: int, concurrency: int = 1) -> dict[str, Any]:
    queries = [q for _ in range(rounds) for q in QUERIES]

    async def one(i: int) -> httpx.Response:
        return await api.http.post(
            f"/api/v1/knowledge-bases/{kb_id}/query", json={"query": queries[i], "topK": 8}
        )

    return await _load(api, concurrency, len(queries), one)


async def section_query(ctx: Context, sizes: Iterable[int] = (10_000, 100_000)) -> None:
    api = ctx.api
    kb = await api.json(
        "POST", "/api/v1/knowledge-bases", json={"name": f"Perf retrieval {uuid.uuid4().hex[:8]}"}
    )
    kb_id = str(kb["id"])
    seeder = Seeder(ctx.stack.db_url, kb_id)
    # Warm the embedder (the first query loads the ONNX model).
    await api.call("POST", f"/api/v1/knowledge-bases/{kb_id}/query", json={"query": "warm up"})
    out: dict[str, Any] = {"embeddingProfile": kb.get("embeddingProfile")}
    probes = seeder.sample_vectors(40)
    for size in sizes:
        seed_seconds = await asyncio.to_thread(seeder.seed, size)
        entry: dict[str, Any] = {
            "chunksInKb": seeder.count,
            "seedSeconds": round(seed_seconds, 1),
            "tableSize": seeder.table_size(),
        }
        exact_ids = []
        sql_ms = []
        for v in probes:
            ids, ms = await asyncio.to_thread(seeder.sql_query, v)
            exact_ids.append(ids)
            sql_ms.append(ms)
        entry["exact"] = {
            "apiC1": await _api_queries(api, kb_id, rounds=5),
            "apiC4": await _api_queries(api, kb_id, rounds=5, concurrency=4),
            "sqlOnlyMs": summarize(sql_ms),
            "plan": (await asyncio.to_thread(seeder.explain, probes[0])).splitlines()[:6],
        }
        build = await asyncio.to_thread(seeder.create_hnsw)
        hnsw_ids, hnsw_ms = [], []
        for v in probes:
            ids, ms = await asyncio.to_thread(seeder.sql_query, v)
            hnsw_ids.append(ids)
            hnsw_ms.append(ms)
        recall = statistics.fmean(
            len(set(a) & set(b)) / max(1, len(a)) for a, b in zip(exact_ids, hnsw_ids, strict=True)
        )
        entry["hnsw"] = {
            "buildSeconds": round(build, 1),
            "tableSizeWithIndex": seeder.table_size(),
            "apiC1": await _api_queries(api, kb_id, rounds=5),
            "apiC4": await _api_queries(api, kb_id, rounds=5, concurrency=4),
            "sqlOnlyMs": summarize(hnsw_ms),
            "recallAt8VsExact": round(recall, 3),
            "plan": (await asyncio.to_thread(seeder.explain, probes[0])).splitlines()[:6],
        }
        await asyncio.to_thread(seeder.drop_hnsw)
        out[str(size)] = entry
        log(
            f"query @{size}: exact API p95 {entry['exact']['apiC1'].get('p95')} ms, "
            f"hnsw API p95 {entry['hnsw']['apiC1'].get('p95')} ms, recall {recall:.3f}"
        )
    ctx.results["query"] = out


# --- driver ---------------------------------------------------------------------------

SECTIONS = ["setup", "api", "dispatch", "models", "ingest", "query"]


def machine() -> dict[str, Any]:
    def sh(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()
        except OSError:
            return ""

    cpu = ""
    for line in sh("lscpu").splitlines():
        if line.startswith("Model name:"):
            cpu = line.split(":", 1)[1].strip()
    mem = ""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            mem = f"{int(line.split()[1]) / 1024**2:.1f} GiB"
    return {
        "cpu": cpu,
        "cpus": os.cpu_count(),
        "memory": mem,
        "arch": platform.machine(),
        "kernel": platform.release(),
        "docker": sh("docker", "version", "--format", "{{.Server.Version}}"),
        "python": platform.python_version(),
        "gitCommit": sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"),
        "date": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


async def measure(
    stack: Stack, only: list[str], out: Path, compose_timings: dict[str, float] | None
) -> dict[str, Any]:
    manifest_path = out.parent / f"{AGENT}.manifest.yaml"
    if not manifest_path.exists():
        manifest_path = await asyncio.to_thread(build_agent_image, stack, out.parent)
    manifest = load_manifest(manifest_path)
    api = Api(stack.base_url)
    ctx = Context(stack=stack, api=api, manifest=manifest)
    ctx.results["machine"] = machine()
    ctx.results["stack"] = {
        "project": stack.project,
        "embeddingMode": stack.embedding_mode,
        "httpPort": stack.http_port,
    }
    try:
        if "setup" in only:
            await section_setup(ctx, compose_timings)
        else:
            await api.login()
            installs = await api.json("GET", "/api/v1/agent-installations")
            ctx.installation_id = installs["items"][0]["id"]
        for name in only:
            if name == "setup":
                continue
            sections: dict[str, Callable[[Context], Awaitable[None]]] = {
                "api": section_api,
                "dispatch": section_dispatch,
                "models": section_models,
                "ingest": section_ingest,
                "query": section_query,
            }
            fn = sections[name]
            try:
                await fn(ctx)
            except Exception as exc:
                log(f"{name} failed: {exc!r}")
                ctx.results[name] = {"error": repr(exc)}
            await asyncio.to_thread(out.write_text, json.dumps(ctx.results, indent=2))
    finally:
        await api.close()
    await asyncio.to_thread(out.write_text, json.dumps(ctx.results, indent=2))
    return ctx.results


# --- report ---------------------------------------------------------------------------


def _verdict(ok: bool | None) -> str:
    return "n/a" if ok is None else ("PASS" if ok else "FAIL")


def report(results: dict[str, Any]) -> str:
    """Markdown summary of a results file: section 22 targets first, then detail."""
    lines = [
        "| PLAN §22 metric | Target | Measured (this machine) | Result |",
        "| --- | --- | --- | --- |",
    ]
    api = results.get("api", {})
    if "c1" in api:
        worst = max(v["p95"] for v in api["c1"].values())
        lines.append(
            f"| API non-inference p95, idle (c=1) | < 300 ms | worst endpoint p95 {worst} ms "
            f"| {_verdict(worst < 300)} |"
        )
    dispatch = results.get("dispatch", {})
    if "sseEventLatencyMs" in dispatch:
        sse = dispatch["sseEventLatencyMs"]
        lines.append(
            f"| UI event visibility after server event | < 2 s | p95 {sse.get('p95')} ms, "
            f"max {sse.get('max')} ms (SSE, n={sse.get('n')}) | "
            f"{_verdict(sse.get('max', 1e9) < 2000)} |"
        )
    if "createdToContainerStartMs" in dispatch:
        start = dispatch["createdToContainerStartMs"]
        running = dispatch["createdToRunningMs"]
        lines.append(
            f"| Agent container start after image present | < 10 s | container start p95 "
            f"{start.get('p95')} ms; SDK handshake (RUNNING) p95 {running.get('p95')} ms | "
            f"{_verdict(running.get('p95', 1e9) < 10_000)} |"
        )
    models = results.get("models", {})
    if "coldStartSeconds" in models:
        cold = models["coldStartSeconds"]
        lines.append(
            f"| Small-model cold start | measured; timeout 10 min | mock model p50 "
            f"{cold['p50']} s, max {cold['max']} s | n/a (mock; GB10 pending) |"
        )
        first = models["chatFirstTokenMs"]
        lines.append(
            f"| Small-model warm first token | measured baseline | mock model p50 "
            f"{first['p50']} ms, p95 {first['p95']} ms (platform overhead only) "
            f"| n/a (GB10 pending) |"
        )
        idle = models["idleUnload"]
        lines.append(
            f"| Idle model unload | within 60 s after grace | {idle['lateBySeconds']} s after a "
            f"{idle['graceSeconds']} s grace | {_verdict(idle['lateBySeconds'] <= 60)} |"
        )
    query = results.get("query", {})
    for size in ("10000", "100000"):
        if size in query:
            exact = query[size]["exact"]["apiC1"]
            hnsw = query[size]["hnsw"]["apiC1"]
            lines.append(
                f"| Knowledge query p95 at {int(size) // 1000}k chunks | < 1 s (target hw) | "
                f"exact {exact['p95']} ms; HNSW {hnsw['p95']} ms | "
                f"{_verdict(exact['p95'] < 1000)} (exact) |"
            )
    setup = results.get("setup", {})
    if setup:
        lines.append(
            f"| Fresh laptop setup after prerequisites | < 15 min excl. downloads | "
            f"{setup['totalSeconds']} s machine time (compose up + API setup + first run) | "
            f"{_verdict(setup['totalSeconds'] < 900)} (machine part only) |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("command", choices=["up", "down", "measure", "all", "report"])
    parser.add_argument("results", nargs="?", type=Path, help="results file for `report`")
    parser.add_argument("--project", default=os.environ.get("PERF_PROJECT", "cqperf"))
    parser.add_argument(
        "--http-port", type=int, default=int(os.environ.get("PERF_HTTP_PORT", 18094))
    )
    parser.add_argument(
        "--postgres-port", type=int, default=int(os.environ.get("PERF_POSTGRES_PORT", 15446))
    )
    parser.add_argument(
        "--registry-port", type=int, default=int(os.environ.get("PERF_REGISTRY_PORT", 15021))
    )
    parser.add_argument("--tag", default=os.environ.get("PERF_TAG", "perf"))
    parser.add_argument(
        "--embedding-mode",
        choices=["local", "fake"],
        default=os.environ.get("PERF_EMBEDDING_MODE", "local"),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("PERF_DATA_DIR", "/tmp/cqperf-data")),  # noqa: S108
    )
    parser.add_argument("--out", type=Path, default=ROOT / "tmp" / "perf" / "results.json")
    parser.add_argument("--only", default=",".join(SECTIONS), help=f"subset of {SECTIONS}")
    parser.add_argument("--keep", action="store_true", help="`all`: leave the stack running")
    args = parser.parse_args()
    stack = Stack(
        project=args.project,
        http_port=args.http_port,
        postgres_port=args.postgres_port,
        registry_port=args.registry_port,
        tag=args.tag,
        embedding_mode=args.embedding_mode,
        data_dir=args.data_dir.resolve(),
    )
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.command == "up":
        print(json.dumps(stack_up(stack)))
    elif args.command == "down":
        stack_down(stack)
    elif args.command == "report":
        print(report(json.loads((args.results or args.out).read_text())))
    elif args.command == "measure":
        asyncio.run(measure(stack, only, args.out, None))
        print(report(json.loads(args.out.read_text())))
    else:
        stack_down(stack)  # start from empty volumes
        # The registry starts empty too, so the agent image is pushed again.
        (args.out.parent / f"{AGENT}.manifest.yaml").unlink(missing_ok=True)
        try:
            # The agent image goes to the stack's registry, so start that first.
            timings = stack_up(stack)
            asyncio.run(measure(stack, only, args.out, timings))
        finally:
            if not args.keep:
                stack_down(stack)
        print(report(json.loads(args.out.read_text())))
        log(f"results: {args.out}")


if __name__ == "__main__":
    main()
