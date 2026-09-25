"""Backup/restore and restart against the real laptop Compose stack (PLAN.md 23.2, 23.6).

Brings up ``infra/compose/compose.yaml`` as a separate project (``cqbak``) on its own
ports and image tags, creates data through the API (owner, installation, a run, a
schedule, a knowledge document, a chat session, an OpenAI key), and then:

* takes backups with the appliance CLI (``infra/debian/bin/crewquarters``, pointed at
  this stack through its CQ_* overrides) and through the owner API;
* destroys every volume (``down -v``: database, documents, and the master key), restores
  with ``crewquarters backup restore --stop``, and checks the data is back: once with the
  master key restored (connections keep working) and once without (connections ask to
  be reconnected instead of failing);
* restarts every service (``docker compose restart``) and does ``down``/``up`` without
  ``-v``, checking that runs, schedules, chat sessions, knowledge and connections survive.

A real host reboot is not possible here: the systemd path (crewquarters-runtime and
crewquarters.service starting the stack at boot) is covered by the .deb install test
(infra/debian/test-install.sh); this test covers the Compose-level restart.

Slow (it builds the platform and proxy images), so it runs only with CQ_STACK_TESTS=1:

    CQ_STACK_TESTS=1 uv run pytest -m docker tests/stack/test_backup_restore_stack.py
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/compose/compose.yaml"
CLI = ROOT / "infra/debian/bin/crewquarters"
PROJECT = "cqbak"
PORT = 18082
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = f"http://localhost:{PORT}"
PASSWORD = "correct horse battery staple"
TAG = "cqbak"

pytestmark = [pytest.mark.docker, pytest.mark.no_db]

if os.environ.get("CQ_STACK_TESTS") != "1" or shutil.which("docker") is None:
    pytest.skip("set CQ_STACK_TESTS=1 (needs Docker; builds images)", allow_module_level=True)

ENV = {
    **os.environ,
    "CQ_HTTP_PORT": str(PORT),
    "CQ_POSTGRES_PORT": "15434",
    "CQ_PUBLIC_BASE_URL": ORIGIN,
    "CQ_PLATFORM_IMAGE": f"crewquarters/platform:{TAG}",
    "CQ_PROXY_IMAGE": f"crewquarters/proxy:{TAG}",
    # The appliance CLI, pointed at this stack.
    "CQ_COMPOSE_FILE": str(COMPOSE_FILE),
    "CQ_COMPOSE_PROJECT": PROJECT,
    "CQ_COMPOSE_NO_ENV_FILES": "1",
    "CQ_DOCUMENTS_SOURCE": f"{PROJECT}_documents",
    "CQ_MASTER_KEY_MOUNT": f"{PROJECT}_master-key:/run/cq-keys",
    "CQ_MASTER_KEY_IN": "/run/cq-keys/master.key",
    "CQ_BACKUP_OWNER": f"{os.getuid()}:{os.getgid()}",
}


def compose(
    *args: str, check: bool = True, timeout: float = 900
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
        env=ENV,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["sh", str(CLI), *args], env=ENV, capture_output=True, text=True, timeout=900
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"crewquarters {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result


def up() -> None:
    compose("up", "-d", "--wait")


def wait_http(within: float = 120) -> None:
    deadline = time.monotonic() + within
    while True:
        try:
            if httpx.get(f"{BASE}/api/v1/health/ready", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise AssertionError("the stack did not become ready")
        time.sleep(1)


def sign_in() -> httpx.Client:
    client = httpx.Client(base_url=BASE, headers={"Origin": ORIGIN}, timeout=30)
    response = client.post("/api/v1/sessions", json={"username": "owner", "password": PASSWORD})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
    return client


def poll(fn: Any, within: float = 60, every: float = 0.5) -> Any:
    deadline = time.monotonic() + within
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(every)


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    compose("down", "-v", "--remove-orphans", check=False)
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "infra/docker/python.Dockerfile",
            "-t",
            ENV["CQ_PLATFORM_IMAGE"],
            ".",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=1800,
    )
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "infra/docker/proxy.Dockerfile",
            "-t",
            ENV["CQ_PROXY_IMAGE"],
            ".",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=1800,
    )
    try:
        up()
        wait_http()
        yield
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
        subprocess.run(
            ["docker", "rmi", "-f", ENV["CQ_PLATFORM_IMAGE"], ENV["CQ_PROXY_IMAGE"]],
            capture_output=True,
            check=False,
        )


def seed() -> dict[str, Any]:
    token = compose("exec", "-T", "control-api", "cq-admin", "bootstrap-token").stdout.strip()
    anon = httpx.Client(base_url=BASE, headers={"Origin": ORIGIN}, timeout=30)
    assert anon.get("/api/v1/bootstrap/status").json() == {"ownerExists": False}
    boot = anon.post(
        "/api/v1/bootstrap", json={"token": token, "username": "owner", "password": PASSWORD}
    )
    assert boot.status_code == 201, boot.text
    assert anon.get("/api/v1/bootstrap/status").json() == {"ownerExists": True}
    c = sign_in()
    model = "local.general.small"
    installed = c.post(f"/api/v1/models/{model}/install")
    assert installed.status_code in (200, 202), installed.text
    poll(
        lambda: c.get(f"/api/v1/models/{model}").json()["downloadState"] == "INSTALLED",
        within=120,
    )
    installation = c.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": {},
            "approvedPermissions": {
                "llmProfiles": ["local.general"],
                "knowledge": [],
                "connectors": {},
                "cloudProviders": [],
                "userInput": True,
            },
        },
    )
    assert installation.status_code == 201, installation.text
    inst_id = installation.json()["id"]
    created = c.post("/api/v1/runs", json={"installationId": inst_id})
    assert created.status_code == 201, created.text
    run = created.json()
    poll(lambda: c.get(f"/api/v1/runs/{run['id']}").json()["state"] == "SUCCEEDED")
    schedule = c.post(
        "/api/v1/schedules",
        json={"installationId": inst_id, "cron": "0 10 * * *", "timezone": "Asia/Kolkata"},
    )
    assert schedule.status_code == 201, schedule.text
    kb = c.post("/api/v1/knowledge-bases", json={"name": "Rehearsal notes"}).json()
    doc = c.post(
        f"/api/v1/knowledge-bases/{kb['id']}/documents",
        files={"file": ("notes.txt", b"The demo starts at ten o'clock sharp.", "text/plain")},
    )
    assert doc.status_code == 202, doc.text
    poll(
        lambda: (
            c.get(f"/api/v1/knowledge-bases/{kb['id']}/documents/{doc.json()['id']}").json()[
                "state"
            ]
            == "READY"
        )
    )
    chat = c.post("/api/v1/chat/sessions", json={"title": "Rehearsal chat"})
    assert chat.status_code == 201, chat.text
    key = c.post(
        "/api/v1/provider-profiles",
        json={
            "provider": "openai",
            "displayName": "Demo key",
            "apiKey": "sk-test-0123456789abcdef",
        },
    )
    assert key.status_code == 201, key.text
    return {
        "run": run["id"],
        "installation": inst_id,
        "schedule": schedule.json()["id"],
        "kb": kb["id"],
        "doc": doc.json()["id"],
        "chat": chat.json()["id"],
    }


def openai_status(c: httpx.Client) -> str:
    items = c.get("/api/v1/connections").json()["items"]
    return next(i["status"] for i in items if i["provider"] == "openai")


def assert_data(ids: dict[str, Any], *, openai: str = "CONNECTED") -> None:
    c = sign_in()
    run = c.get(f"/api/v1/runs/{ids['run']}")
    assert run.status_code == 200 and run.json()["state"] == "SUCCEEDED"
    assert c.get(f"/api/v1/agent-installations/{ids['installation']}").status_code == 200
    schedules = c.get("/api/v1/schedules").json()["items"]
    assert [s["id"] for s in schedules] == [ids["schedule"]]
    assert schedules[0]["timezone"] == "Asia/Kolkata"
    doc = c.get(f"/api/v1/knowledge-bases/{ids['kb']}/documents/{ids['doc']}")
    assert doc.status_code == 200 and doc.json()["state"] == "READY"
    hits = c.post(
        f"/api/v1/knowledge-bases/{ids['kb']}/query", json={"query": "when does the demo start"}
    )
    assert hits.status_code == 200, hits.text
    assert c.get(f"/api/v1/chat/sessions/{ids['chat']}").status_code == 200
    assert poll(lambda: openai_status(c) == openai, within=30)


def test_backup_destroy_restore_and_restart(stack: None, tmp_path: Path) -> None:
    try:
        scenario(tmp_path)
    finally:
        # Root-owned archives (the one holding the master key): hand them back so pytest
        # can clean up its temporary directory.
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "-v",
                f"{tmp_path}:/t",
                ENV["CQ_PLATFORM_IMAGE"],
                "chown",
                "-R",
                f"{os.getuid()}:{os.getgid()}",
                "/t",
            ],
            capture_output=True,
            check=False,
        )


def scenario(tmp_path: Path) -> None:
    ids = seed()
    out = tmp_path / "backups"
    device_dir = tmp_path / "device-backups"  # stands in for /var/lib/crewquarters/backups
    ENV["CQ_BACKUP_HOST_DIR"] = str(device_dir)

    # Device backup with the master key (the CLI warns loudly), and one without.
    with_key = cli("backup", "create", "--out", str(out), "--include-master-key", "--label", "key")
    assert "WARNING" in with_key.stderr and "master key" in with_key.stderr
    cli("backup", "create", "--out", str(out), "--label", "nokey")
    archives = {p.name: p for p in out.glob("*.tar.gz")}
    key_archive = next(p for n, p in archives.items() if n.endswith("-key.tar.gz"))
    plain_archive = next(p for n, p in archives.items() if n.endswith("-nokey.tar.gz"))
    for archive in (key_archive, plain_archive):
        assert archive.stat().st_mode & 0o777 == 0o600
    # The archive with the key stays root-only (0600): inspect it through the CLI.
    assert "master key:       INCLUDED" in cli("backup", "verify", str(key_archive)).stdout
    with tarfile.open(plain_archive) as tar:
        assert "master.key" not in tar.getnames()
    cli("backup", "verify", str(plain_archive))

    # Owner API backup: queued -> succeeded by the control API's worker; downloadable.
    c = sign_in()
    created = c.post("/api/v1/system/backups")
    assert created.status_code == 202, created.text
    item = poll(
        lambda: next(
            (
                i
                for i in c.get("/api/v1/system/backups").json()["items"]
                if i["id"] == created.json()["id"] and i["status"] == "succeeded"
            ),
            None,
        ),
        within=120,
    )
    assert item["includesMasterKey"] is False and item["downloadable"]
    download = c.get(f"/api/v1/system/backups/{item['id']}/download")
    assert download.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(download.content)) as tar:
        assert set(tar.getnames()) == {"manifest.json", "database.dump", "documents.tar"}

    # Diagnostics through the appliance CLI: host logs included, redacted.
    diag = cli("diagnostics", "--out", str(tmp_path / "diag"), "--tail", "200")
    assert "Diagnostics written" in diag.stdout
    bundle = next((tmp_path / "diag").glob("*.zip"))
    with zipfile.ZipFile(bundle) as z:
        names = set(z.namelist())
        assert "host/compose-ps.txt" in names and "host/logs/control-api.log" in names
        everything = "".join(z.read(n).decode(errors="replace") for n in names)
    assert "sk-test-0123456789abcdef" not in everything
    assert PASSWORD not in everything

    # Restore refuses while the platform is running.
    refused = cli("backup", "restore", str(key_archive), "--yes", check=False)
    assert refused.returncode == 3 and "--stop" in refused.stderr

    # Destroy everything (database, documents, master key), start fresh, restore with key.
    compose("down", "-v")
    up()
    wait_http()
    restored = cli("backup", "restore", str(key_archive), "--stop", "--with-master-key", "--yes")
    assert "master key was restored" in restored.stdout, restored.stdout
    assert list(device_dir.glob("crewquarters-backup-*-pre-restore.tar.gz"))
    up()
    wait_http()
    assert_data(ids, openai="CONNECTED")

    # Again without the key on a fresh device key: connections ask to be reconnected.
    compose("down", "-v")
    up()
    wait_http()
    restored = cli("backup", "restore", str(plain_archive), "--stop", "--yes", "--no-pre-backup")
    assert "Reconnect in Connections: openai" in restored.stdout
    up()
    wait_http()
    assert_data(ids, openai="NEEDS_ATTENTION")

    # Restart every service; then down/up without -v. Everything survives.
    compose("restart")
    wait_http()
    assert_data(ids, openai="NEEDS_ATTENTION")
    compose("down")
    up()
    wait_http()
    assert_data(ids, openai="NEEDS_ATTENTION")
