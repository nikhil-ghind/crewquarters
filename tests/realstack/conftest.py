"""Real-stack E2E fixtures (docs/testing-realstack.md).

Everything an owner does goes through the proxy exactly as the web UI does it: a session
cookie, the ``X-CSRF-Token`` header and an allowed ``Origin``. The only other doors are
test-only and read-mostly:

* the broker harness's ``/__realstack`` admin (fake Google/Twilio fixtures and faults), and
* Docker/Compose, to inspect agent containers and to stop/start services for failure
  injection.

Start the stack first with ``make realstack-up``; the suite skips if it is not running.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("CQ_REALSTACK_URL", "http://localhost:18083")
ADMIN_URL = os.environ.get("CQ_REALSTACK_BROKER_ADMIN_URL", "http://127.0.0.1:18084")
ADMIN_TOKEN = os.environ.get("CQ_REALSTACK_ADMIN_TOKEN", "realstack-admin-token-0000000000")
PROJECT = os.environ.get("CQ_REALSTACK_PROJECT", "cqreal")
AGENT_NETWORK = "cqreal-agents"
USERNAME = "owner"
PASSWORD = "realstack-owner-password-1"
MODEL = "local.general.small"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}
COMPOSE = [
    "docker",
    "compose",
    "-p",
    PROJECT,
    "--env-file",
    str(REPO / "infra/compose/realstack.env"),
    "-f",
    str(REPO / "infra/compose/compose.yaml"),
    "-f",
    str(REPO / "infra/compose/compose.runtime.yaml"),
    "-f",
    str(REPO / "infra/compose/compose.realstack.yaml"),
]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.realstack)
            item.add_marker(pytest.mark.no_db)  # the root clean_db fixture must not run


def wait_for(
    probe: Callable[[], Any], what: str, timeout: float = 60, interval: float = 0.5
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}; last={last!r}")


def error_code(response: httpx.Response) -> str | None:
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return None


# --- Docker / Compose ------------------------------------------------------------------


class Stack:
    """The containers behind the API: inspection and failure injection."""

    def compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*COMPOSE, *args], capture_output=True, text=True, check=check, timeout=300
        )

    def stop(self, service: str, grace: int = 5) -> None:
        self.compose("stop", "-t", str(grace), service)

    def kill(self, service: str) -> None:
        self.compose("kill", "-s", "KILL", service)

    def start(self, service: str, wait: bool = True) -> None:
        self.compose("start", service)
        if wait:
            self.compose("up", "-d", "--wait", "--no-recreate", service)

    def container(self, service: str) -> str:
        out = self.compose("ps", "-q", service).stdout.split()
        assert out, f"{service} is not running"
        return out[0]

    def psql(self, sql: str) -> list[list[str]]:
        """A read-only query, for state the API does not show (for example attempts)."""
        out = self.compose(
            "exec", "-T", "postgres", "psql", "-U", "crewquarters", "-At", "-F", "|", "-c", sql
        ).stdout
        return [line.split("|") for line in out.splitlines() if line]

    def inspect(self, name: str) -> dict[str, Any] | None:
        done = subprocess.run(
            ["docker", "inspect", name], capture_output=True, text=True, check=False
        )
        if done.returncode != 0:
            return None
        return dict(json.loads(done.stdout)[0])

    def run_container(self, run_id: str, attempt: int = 1) -> str:
        return f"cq-run-{run_id}-{attempt}"

    def run_containers(self, run_id: str) -> list[str]:
        out = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=io.crewquarters.run={run_id}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return sorted(out.split())

    def service_ip(self, service: str) -> str:
        info = self.inspect(self.container(service))
        assert info is not None
        networks = info["NetworkSettings"]["Networks"]
        return str(networks[f"{PROJECT}_default"]["IPAddress"])

    def exec_python(self, container: str, program: str, *args: str) -> dict[str, Any]:
        done = subprocess.run(
            ["docker", "exec", container, "python", "-c", program, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        return dict(json.loads(done.stdout))

    def logs(self, service: str, since: str = "10m") -> str:
        return self.compose("logs", "--no-color", "--since", since, service).stdout


# --- Broker harness (fake Google/Twilio) -------------------------------------------------


class Fakes:
    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=ADMIN_URL, headers={"X-Realstack-Admin": ADMIN_TOKEN}, timeout=30
        )

    def state(self) -> dict[str, Any]:
        response = self.http.get("/__realstack/state")
        response.raise_for_status()
        return dict(response.json())

    def seed_sheet(self, spreadsheet_id: str, tabs: dict[str, list[list[str]]]) -> None:
        self.http.put(f"/__realstack/sheets/{spreadsheet_id}", json=tabs).raise_for_status()

    def faults(self, **faults: Any) -> None:
        self.http.put("/__realstack/faults", json=faults).raise_for_status()

    def expire_google(self) -> None:
        self.http.post("/__realstack/google/expire").raise_for_status()

    def reset(self) -> None:
        self.http.post("/__realstack/reset").raise_for_status()


# --- The owner, through the proxy ----------------------------------------------------------


class Owner:
    """An owner's browser session: cookie + CSRF + Origin on every state change."""

    wait_for = staticmethod(wait_for)

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.http = httpx.Client(base_url=BASE_URL, headers={"Origin": BASE_URL}, timeout=60)
        self.sign_in()

    def sign_in(self) -> None:
        login = self.http.post(
            "/api/v1/sessions", json={"username": USERNAME, "password": PASSWORD}
        )
        if login.status_code != 201:
            token = self.stack.compose(
                "exec", "-T", "control-api", "cq-admin", "bootstrap-token"
            ).stdout.split()[-1]
            login = self.http.post(
                "/api/v1/bootstrap",
                json={"token": token, "username": USERNAME, "password": PASSWORD},
            )
        assert login.status_code in (200, 201), login.text
        self.http.headers["X-CSRF-Token"] = login.json()["csrfToken"]

    # plain verbs -----------------------------------------------------------------------
    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(path, **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.post(path, **kw)

    def put(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.put(path, **kw)

    def ok(self, response: httpx.Response) -> dict[str, Any]:
        assert response.status_code < 300, f"{response.request.url}: {response.text}"
        return dict(response.json())

    # catalog, installations, runs --------------------------------------------------------
    def manifest(self, agent_id: str) -> dict[str, Any]:
        return self.ok(self.get(f"/api/v1/catalog/agents/{agent_id}"))

    def install(
        self, agent_id: str, config: dict[str, Any], permissions: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        agent = self.manifest(agent_id)
        requested = agent["latest"]["permissions"]
        body = {
            "agentId": agent_id,
            "config": config,
            "approvedPermissions": permissions if permissions is not None else requested,
        }
        return self.ok(self.post("/api/v1/agent-installations", json=body))

    def start(self, installation_id: str) -> dict[str, Any]:
        return self.ok(self.post("/api/v1/runs", json={"installationId": installation_id}))

    def run(self, run_id: str) -> dict[str, Any]:
        return self.ok(self.get(f"/api/v1/runs/{run_id}"))

    def wait_state(
        self, run_id: str, states: set[str] | str, timeout: float = 120
    ) -> dict[str, Any]:
        wanted = {states} if isinstance(states, str) else states

        def probe() -> dict[str, Any] | None:
            run = self.run(run_id)
            if run["state"] in wanted:
                return run
            if run["state"] in TERMINAL - wanted:
                raise AssertionError(
                    f"run {run_id} ended {run['state']} (wanted {wanted}): {run['error']}\n"
                    + self.debug(run_id)
                )
            return None

        return dict(wait_for(probe, f"run {run_id} in {wanted}", timeout))

    def events(self, run_id: str) -> list[dict[str, Any]]:
        response = self.get(f"/api/v1/runs/{run_id}/events/history", params={"limit": 500})
        assert response.status_code == 200, response.text
        body = response.json()
        return list(body["items"] if isinstance(body, dict) else body)

    def states(self, run_id: str) -> list[str]:
        return [e["payload"]["to"] for e in self.events(run_id) if e["type"] == "run.state_changed"]

    def pending_input(self, run_id: str, timeout: float = 90) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            items = self.ok(
                self.get("/api/v1/input-requests", params={"runId": run_id, "state": "pending"})
            )["items"]
            if items:
                return dict(items[0])
            run = self.run(run_id)
            if run["state"] in TERMINAL:
                raise AssertionError(f"run ended {run['state']}: {run['error']}")
            return None

        return dict(wait_for(probe, f"an input request for run {run_id}", timeout))

    def answer(self, request: dict[str, Any], value: Any) -> httpx.Response:
        return self.post(
            f"/api/v1/input-requests/{request['id']}/answer",
            json={"version": request["version"], "value": value},
        )

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self.ok(self.post(f"/api/v1/runs/{run_id}/cancel"))

    def retry(self, run_id: str) -> dict[str, Any]:
        return self.ok(self.post(f"/api/v1/runs/{run_id}/retry"))

    def connection(self, provider: str) -> dict[str, Any]:
        items = self.ok(self.get("/api/v1/connections"))["items"]
        return dict(next(c for c in items if c["provider"] == provider))

    def debug(self, run_id: str) -> str:
        """Run events plus the agent container's logs, for assertion messages."""
        lines = [f"{e['type']}: {json.dumps(e['payload'])[:300]}" for e in self.events(run_id)]
        for name in self.stack.run_containers(run_id):
            logs = subprocess.run(
                ["docker", "logs", "--tail", "40", name], capture_output=True, text=True
            )
            lines.append(f"--- {name} ---\n{logs.stdout[-3000:]}{logs.stderr[-3000:]}")
        return "\n".join(lines)

    # connections ---------------------------------------------------------------------------
    def connect_google(self, scope: str) -> httpx.Response:
        """The UI's Connect button, then Google redirecting the browser back (fake consent)."""
        start = self.ok(
            self.post("/api/v1/connections/google/start", json={"capabilities": [scope]})
        )
        state = parse_qs(urlsplit(start["authorizationUrl"]).query)["state"][0]
        return self.get(
            "/api/v1/connections/google/callback",
            params={"state": state, "code": f"fake-code:{scope}"},
            follow_redirects=False,
        )


def _stack_is_up() -> bool:
    try:
        ready = httpx.get(f"{BASE_URL}/api/v1/health/ready", timeout=5)
        admin = httpx.get(
            f"{ADMIN_URL}/__realstack/state",
            headers={"X-Realstack-Admin": ADMIN_TOKEN},
            timeout=5,
        )
    except httpx.HTTPError:
        return False
    return ready.status_code == 200 and admin.status_code == 200


@pytest.fixture(scope="session")
def stack() -> Stack:
    if not _stack_is_up():
        pytest.skip(f"the real stack is not running at {BASE_URL}; run `make realstack-up`")
    return Stack()


@pytest.fixture(scope="session")
def fakes(stack: Stack) -> Iterator[Fakes]:
    client = Fakes()
    yield client
    client.faults()
    client.http.close()


@pytest.fixture(scope="session")
def owner(stack: Stack) -> Iterator[Owner]:
    client = Owner(stack)
    model = client.ok(client.get(f"/api/v1/models/{MODEL}"))
    if model["downloadState"] != "INSTALLED":
        client.post(f"/api/v1/models/{MODEL}/install")
        wait_for(
            lambda: (
                client.ok(client.get(f"/api/v1/models/{MODEL}"))["downloadState"] == "INSTALLED"
            ),
            f"{MODEL} installed",
            120,
        )
    yield client
    client.http.close()


@pytest.fixture
def healthy(stack: Stack, fakes: Fakes, owner: Owner) -> Iterator[None]:
    """Every service up and no faults, before and after a test (failure tests restore it)."""
    fakes.faults()
    yield
    for service in ("capability-broker", "model-gateway", "scheduler"):
        if not stack.compose("ps", "-q", "--status", "running", service).stdout.strip():
            stack.start(service)
    fakes.faults()


@pytest.fixture
def google(owner: Owner, fakes: Fakes, healthy: None) -> dict[str, Any]:
    """A CONNECTED Google connection holding gmail.readonly and spreadsheets, each granted
    through its own consent (they accumulate on one connection)."""
    # "Test" first (a refresh now): a grant the fake Google no longer knows, e.g. after the
    # broker container was recreated, turns NEEDS_ATTENTION instead of failing a run later.
    if owner.connection("google")["status"] == "CONNECTED":
        owner.post("/api/v1/connections/google/test")
    connection = owner.connection("google")
    granted = set(connection.get("grantedCapabilities") or [])
    if connection["status"] != "CONNECTED" or not {"gmail.readonly", "spreadsheets"} <= granted:
        for scope in ("gmail.readonly", "spreadsheets"):
            redirect = owner.connect_google(scope)
            assert redirect.status_code == 303, redirect.text
            assert redirect.headers["location"].endswith("/connections/google?result=connected")
        # The control API caches connection status for 2 s.
        connection = wait_for(
            lambda: (
                (c := owner.connection("google"))["status"] == "CONNECTED"
                and {"gmail.readonly", "spreadsheets"} <= set(c["grantedCapabilities"])
                and c
            ),
            "Google CONNECTED with gmail.readonly and spreadsheets",
            10,
        )
    return connection


@pytest.fixture
def knowledge_base(owner: Owner) -> dict[str, Any]:
    """A knowledge base with one indexed document, created through the API."""
    kb = owner.ok(
        owner.post("/api/v1/knowledge-bases", json={"name": f"probe-{uuid.uuid4().hex[:8]}"})
    )
    text = (
        "Contract probe knowledge search. The Crewquarters contract probe verifies that "
        "knowledge search returns cited passages from documents the owner uploaded.\n"
    ) * 3
    doc = owner.ok(
        owner.post(
            f"/api/v1/knowledge-bases/{kb['id']}/documents",
            files={"file": ("probe.md", text.encode(), "text/markdown")},
        )
    )
    wait_for(
        lambda: (
            owner.ok(owner.get(f"/api/v1/knowledge-bases/{kb['id']}/documents/{doc['id']}"))[
                "state"
            ]
            == "READY"
        ),
        "the document to be indexed",
        120,
    )
    return kb
