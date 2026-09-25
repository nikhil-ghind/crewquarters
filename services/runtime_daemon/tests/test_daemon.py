"""Runtime daemon tests against the local Docker engine (real containers).

The isolation tests are the negative network/privilege evidence required by
PLAN.md section 21 ("Runtime: container hardening and unreachable prohibited targets").
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from crewquarters_runtime.config import DaemonConfig
from crewquarters_runtime.docker_api import DockerClient
from crewquarters_runtime.engine import Engine
from crewquarters_runtime.server import DaemonServer

ROOT = Path(__file__).resolve().parents[3]
BUSYBOX = "busybox@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
TOKEN = "test-daemon-token-0000000000000000"

pytestmark = [pytest.mark.no_db, pytest.mark.docker]


def docker_available() -> bool:
    try:
        return DockerClient(Path("/var/run/docker.sock"), timeout=5).ping()
    except OSError:
        return False


if not docker_available():  # pragma: no cover
    pytest.skip("Docker is not available", allow_module_level=True)


class Daemon:
    def __init__(self, tmp: Path, profiles: Path, hf_endpoint: str = "http://127.0.0.1:9") -> None:
        suffix = uuid.uuid4().hex[:6]
        self.cfg = DaemonConfig(
            socket_path=tmp / "runtime.sock",
            token=TOKEN,
            token_file=None,
            data_dir=tmp / "data",
            model_profiles_dir=profiles,
            agent_network=f"cq-agents-t{suffix}",
            model_network=f"cq-models-t{suffix}",
            hf_endpoint=hf_endpoint,
            disk_reserve_bytes=0,
            isolate_model_network=False,  # the test process calls model containers directly
        )
        self.engine = Engine(self.cfg)
        self.engine.ensure_networks()
        self.server = DaemonServer(self.engine, TOKEN, None, self.cfg.socket_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds=str(self.cfg.socket_path)),
            base_url="http://runtime",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=120,
        )

    def close(self) -> None:
        docker = self.engine.docker
        for kind in ("run", "model"):
            for c in docker.container_list({"io.crewquarters.kind": kind}):
                name = c["Names"][0].lstrip("/")
                if self.cfg.agent_network in json.dumps(c) or self.cfg.model_network in json.dumps(
                    c
                ):
                    docker.container_remove(name)
        for profile in self.engine.profiles.values():
            docker.container_remove(profile.container_name)
        for net in (self.cfg.agent_network, self.cfg.model_network):
            subprocess.run(["docker", "network", "rm", net], capture_output=True, check=False)
        self.server.shutdown()
        self.server.server_close()
        self.client.close()


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Daemon]:
    d = Daemon(tmp_path, ROOT / "catalog/models/dev")
    try:
        yield d
    finally:
        d.close()


def run_spec(entrypoint: list[str], **overrides: Any) -> dict[str, Any]:
    spec = {
        "run_id": str(uuid.uuid4()),
        "attempt": 1,
        "installation_id": str(uuid.uuid4()),
        "agent_version_id": str(uuid.uuid4()),
        "image": BUSYBOX,
        "entrypoint": entrypoint,
        "architectures": ["linux/amd64", "linux/arm64"],
        "cpu": 0.5,
        "memory_mb": 128,
        "pids": 64,
        "env": {"PLATFORM_RUN_ID": "r", "PLATFORM_ATTEMPT": "1"},
        "config": {"hello": "crew"},
    }
    spec.update(overrides)
    return spec


def wait_exited(daemon: Daemon, ref: str, timeout: float = 60) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = daemon.client.get(f"/internal/v1/runs/{ref}").json()
        if status["state"] == "exited":
            return dict(status)
        time.sleep(0.2)
    raise AssertionError(f"{ref} did not exit")


def test_requires_service_token(daemon: Daemon) -> None:
    anonymous = httpx.Client(transport=httpx.HTTPTransport(uds=str(daemon.cfg.socket_path)))
    response = anonymous.get("http://runtime/internal/v1/host/capacity")
    assert response.status_code == 401
    assert oct(daemon.cfg.socket_path.stat().st_mode & 0o777) == "0o660"


def test_capacity_reports_host(daemon: Daemon) -> None:
    capacity = daemon.client.get("/internal/v1/host/capacity").json()
    assert capacity["architecture"] in ("linux/amd64", "linux/arm64")
    assert capacity["memory"]["totalBytes"] > 0
    assert capacity["docker"]["available"] is True
    assert "gpu" in capacity and "nvidiaRuntime" in capacity["docker"]
    assert capacity["networks"][daemon.cfg.agent_network] == "isolated"


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"image": "busybox:latest"}, "MUTABLE_IMAGE"),
        ({"env": {"DOCKER_HOST": "tcp://x"}}, "ENV_NOT_ALLOWED"),
        ({"cpu": 64}, "RESOURCE_LIMIT"),
        ({"memory_mb": 10}, "RESOURCE_LIMIT"),
        ({"entrypoint": []}, "INVALID_RUN_SPEC"),
        ({"architectures": ["linux/s390x"]}, "ARCH_UNSUPPORTED"),
    ],
)
def test_rejects_unsafe_specs(daemon: Daemon, override: dict[str, Any], code: str) -> None:
    spec = run_spec(["true"])
    spec.update(override)
    response = daemon.client.post("/internal/v1/runs", json=spec)
    assert response.status_code in (409, 422)
    assert response.json()["error"]["code"] == code


def test_unknown_fields_cannot_reach_docker(daemon: Daemon) -> None:
    spec = run_spec(["sleep", "30"])
    spec.update(
        {"privileged": True, "binds": ["/:/host"], "network_mode": "host", "cap_add": ["SYS_ADMIN"]}
    )
    ref = daemon.client.post("/internal/v1/runs", json=spec).json()["runtimeRef"]
    info = daemon.engine.docker.container_inspect(ref)
    assert info is not None
    host = info["HostConfig"]
    assert host["Privileged"] is False
    assert host["NetworkMode"] == daemon.cfg.agent_network
    assert host["CapAdd"] in (None, [])
    assert all(":/host" not in b for b in host["Binds"])
    daemon.client.post(f"/internal/v1/runs/{ref}/cancel", json={"graceSeconds": 0})


def test_hardened_container_cannot_reach_prohibited_targets(daemon: Daemon) -> None:
    postgres_ip = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
            "crewquarters-postgres-1",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    target_db = postgres_ip[0] if postgres_ip else "172.17.0.2"
    docker0 = (
        subprocess.run(
            ["docker", "network", "inspect", "bridge", "-f", "{{(index .IPAM.Config 0).Gateway}}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or "172.17.0.1"
    )
    host_ip = subprocess.run(
        ["hostname", "-I"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    host_lan = host_ip[0] if host_ip else "192.0.2.1"
    script = f"""
check() {{ if "$@" >/dev/null 2>&1; then echo "CHECK $NAME=reachable"; else echo "CHECK $NAME=blocked"; fi; }}
echo "UID=$(id -u)"
touch /rootfs-write 2>/dev/null && echo "ROOTFS=writable" || echo "ROOTFS=readonly"
touch /tmp/ok 2>/dev/null && echo "TMP=writable" || echo "TMP=readonly"
echo "CONFIG=$(cat $PLATFORM_CONFIG_FILE)"
echo "CAPEFF=$(grep CapEff /proc/self/status | awk '{{print $2}}')"
echo "NONEWPRIVS=$(grep NoNewPrivs /proc/self/status | awk '{{print $2}}')"
NAME=internet check wget -q -T 3 -O /dev/null http://1.1.1.1
NAME=dns check nslookup -timeout=2 example.com
NAME=database check nc -z -w 3 {target_db} 5432
NAME=hostgateway check nc -z -w 3 {docker0} 22
NAME=hostlan check nc -z -w 3 {host_lan} 22
NAME=hostdns check nc -z -w 3 {host_lan} 53
NAME=dockersock check ls /var/run/docker.sock
"""
    encoded = base64.b64encode(script.encode()).decode()
    runner = 'grep -o "[A-Za-z0-9+/=]\\{40,\\}" "$PLATFORM_CONFIG_FILE" | base64 -d | sh'
    created = daemon.client.post(
        "/internal/v1/runs", json=run_spec(["sh", "-c", runner], config={"script": encoded})
    )
    assert created.status_code == 201, created.text
    ref = created.json()["runtimeRef"]
    status = wait_exited(daemon, ref)
    assert status["exitCode"] == 0
    logs = daemon.client.get(f"/internal/v1/runs/{ref}/logs", params={"tail": 50}).json()
    out = {
        line["line"].split(" ", 1)[1].split("=")[0].replace("CHECK ", ""): line["line"].split(
            "=", 1
        )[1]
        for line in logs["lines"]
        if "=" in line["line"]
    }
    text = "\n".join(line["line"] for line in logs["lines"])
    assert "UID=65532" in text
    assert "ROOTFS=readonly" in text and "TMP=writable" in text
    assert 'CONFIG={"script": ' in text
    assert "CAPEFF=0000000000000000" in text
    assert "NONEWPRIVS=1" in text
    for target in (
        "internet",
        "dns",
        "database",
        "hostgateway",
        "hostlan",
        "hostdns",
        "dockersock",
    ):
        assert f"CHECK {target}=blocked" in text, (target, text, out)


def test_start_is_idempotent_and_cancel_stops(daemon: Daemon) -> None:
    spec = run_spec(["sleep", "300"])
    first = daemon.client.post("/internal/v1/runs", json=spec).json()
    second = daemon.client.post("/internal/v1/runs", json=spec).json()
    assert first["runtimeRef"] == second["runtimeRef"]
    assert first["created"] is True and second["created"] is False
    ref = first["runtimeRef"]
    assert daemon.client.get(f"/internal/v1/runs/{ref}").json()["state"] == "running"
    started = time.time()
    cancelled = daemon.client.post(
        f"/internal/v1/runs/{ref}/cancel", json={"graceSeconds": 1}
    ).json()
    assert cancelled["state"] == "exited" and time.time() - started < 20
    assert daemon.client.get(f"/internal/v1/runs/{ref}").status_code == 404
    assert not (daemon.cfg.runs_dir / spec["run_id"]).exists()


def test_status_reports_a_crash_exit_code(daemon: Daemon) -> None:
    """The control plane's exit watcher fails a run from this status (AGENT_EXITED)."""
    ref = daemon.client.post("/internal/v1/runs", json=run_spec(["sh", "-c", "exit 3"])).json()[
        "runtimeRef"
    ]
    status = wait_exited(daemon, ref)
    assert status["exitCode"] == 3 and status["oomKilled"] is False
    assert status["finishedAt"] and not status["finishedAt"].startswith("0001-")
    assert status["memoryLimitBytes"] == 128 * 1024 * 1024


def test_status_reports_an_out_of_memory_kill(daemon: Daemon) -> None:
    """``tail /dev/zero`` buffers an endless line until the kernel kills it at the 64 MiB
    limit; the status carries what AGENT_OUT_OF_MEMORY reports.

    Docker itself loses the OOM flag for roughly 1 in 12 kills (exit 137, OOMKilled=false;
    the control plane treats that as a probable OOM), so up to three containers are tried
    until one shows that the daemon passes the flag through."""
    statuses = []
    for _ in range(3):
        spec = run_spec(["tail", "/dev/zero"], memory_mb=64)
        ref = daemon.client.post("/internal/v1/runs", json=spec).json()["runtimeRef"]
        status = wait_exited(daemon, ref)
        assert status["exitCode"] == 137, status
        assert status["memoryLimitBytes"] == 64 * 1024 * 1024
        statuses.append(status)
        if status["oomKilled"]:
            break
    assert statuses[-1]["oomKilled"] is True, statuses


def test_status_of_a_running_container_has_no_exit(daemon: Daemon) -> None:
    ref = daemon.client.post("/internal/v1/runs", json=run_spec(["sleep", "300"])).json()[
        "runtimeRef"
    ]
    status = daemon.client.get(f"/internal/v1/runs/{ref}").json()
    assert status["state"] == "running"
    assert status["exitCode"] is None and status["finishedAt"] is None
    assert status["oomKilled"] is False


def test_invalid_runtime_ref_is_rejected(daemon: Daemon) -> None:
    for ref in ("crewquarters-postgres-1", "cq-model-local-general-small", "../../etc"):
        assert daemon.client.get(f"/internal/v1/runs/{ref}").status_code == 404
        assert daemon.client.post(f"/internal/v1/runs/{ref}/cancel", json={}).status_code == 404


def test_gc_removes_old_exited_containers(daemon: Daemon) -> None:
    ref = daemon.client.post("/internal/v1/runs", json=run_spec(["true"])).json()["runtimeRef"]
    wait_exited(daemon, ref)
    assert ref in daemon.engine.gc(older_than_seconds=0)
    assert daemon.client.get(f"/internal/v1/runs/{ref}").status_code == 404


def test_model_install_start_serve_stop_delete(daemon: Daemon) -> None:
    model = "local.general.small"
    assert (
        daemon.client.get(f"/internal/v1/models/{model}").json()["files"]["state"]
        == "NOT_INSTALLED"
    )
    not_ready = daemon.client.post(f"/internal/v1/models/{model}/start")
    assert (
        not_ready.status_code == 409 and not_ready.json()["error"]["code"] == "MODEL_NOT_INSTALLED"
    )

    daemon.client.post(f"/internal/v1/models/{model}/install")
    daemon.engine.store.wait(model, 30)
    files = daemon.client.get(f"/internal/v1/models/{model}").json()["files"]
    assert files["state"] == "INSTALLED" and files["manifestSha256"]
    installed = Path(files["path"])
    assert (installed / "crewquarters-manifest.json").exists()

    state = daemon.client.post(f"/internal/v1/models/{model}/start").json()
    endpoint = state["container"]["endpoint"]
    assert state["container"]["state"] == "running" and endpoint["ip"]
    deadline = time.time() + 30
    served = None
    while time.time() < deadline:
        try:
            served = httpx.get(
                f"http://{endpoint['ip']}:{endpoint['port']}/v1/models", timeout=2
            ).json()
            break
        except httpx.HTTPError:
            time.sleep(0.3)
    assert served and served["data"][0]["id"] == model
    again = daemon.client.post(f"/internal/v1/models/{model}/start").json()  # idempotent
    assert again["container"]["startedAt"] == state["container"]["startedAt"]

    busy = daemon.client.delete(f"/internal/v1/models/{model}/files")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "MODEL_RESIDENT"
    started_stop = time.time()
    stopped = daemon.client.post(
        f"/internal/v1/models/{model}/stop", json={"graceSeconds": 30}
    ).json()
    assert time.time() - started_stop < 10  # init forwards SIGTERM; no 30 s kill wait
    assert stopped["containerRemoved"] is True
    assert (
        daemon.client.get(f"/internal/v1/models/{model}").json()["container"]["state"] == "absent"
    )
    deleted = daemon.client.delete(f"/internal/v1/models/{model}/files").json()
    assert deleted["state"] == "NOT_INSTALLED" and not installed.exists()


def test_unknown_model_is_not_allowlisted(daemon: Daemon) -> None:
    response = daemon.client.post("/internal/v1/models/evil.model/start")
    assert response.status_code == 404


# --- Hugging Face download path (fake hub) ---------------------------------------------

FILES = {"config.json": b'{"a": 1}', "model.safetensors": bytes(range(256)) * 4000}


class FakeHub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    corrupt = False

    def log_message(self, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.startswith("/api/models/"):
            tree = [
                {
                    "type": "file",
                    "path": name,
                    "size": len(data),
                    **(
                        {"lfs": {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)}}
                        if name.endswith(".safetensors")
                        else {}
                    ),
                }
                for name, data in FILES.items()
            ] + [{"type": "file", "path": "README.md", "size": 3}]
            body = json.dumps(tree).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        name = self.path.rsplit("/", 1)[-1]
        data = FILES.get(name, b"")
        if FakeHub.corrupt and name.endswith(".safetensors"):
            data = b"x" * len(data)
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        chunk = data[start:]
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


@pytest.fixture
def hub_daemon(tmp_path: Path) -> Iterator[Daemon]:
    hub = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
    threading.Thread(target=hub.serve_forever, daemon=True).start()
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile = json.loads((ROOT / "catalog/models/dgx/local.general.small.json").read_text())
    profile["source"] = {
        "type": "huggingface",
        "repo": "acme/tiny",
        "revision": "a" * 40,
        "allowPatterns": ["*.json", "*.safetensors"],
    }
    (profiles / "local.general.small.json").write_text(json.dumps(profile))
    d = Daemon(tmp_path, profiles, hf_endpoint=f"http://127.0.0.1:{hub.server_address[1]}")
    try:
        yield d
    finally:
        d.close()
        hub.shutdown()
        FakeHub.corrupt = False


def test_hf_download_resumes_and_verifies(hub_daemon: Daemon) -> None:
    profile = hub_daemon.engine.profiles["local.general.small"]
    staging = hub_daemon.engine.store.staging_path(profile)
    staging.mkdir(parents=True)
    weights = FILES["model.safetensors"]
    (staging / "model.safetensors").write_bytes(weights[:100_000])  # partial download
    hub_daemon.client.post("/internal/v1/models/local.general.small/install")
    hub_daemon.engine.store.wait("local.general.small", 30)
    files = hub_daemon.client.get("/internal/v1/models/local.general.small").json()["files"]
    assert files["state"] == "INSTALLED", files
    path = Path(files["path"])
    assert (path / "model.safetensors").read_bytes() == weights
    assert not (path / "README.md").exists()  # outside allowPatterns
    manifest = json.loads((path / "crewquarters-manifest.json").read_text())
    assert {f["path"] for f in manifest["files"]} == {"config.json", "model.safetensors"}
    assert files["bytesDone"] == files["bytesTotal"] == sum(len(v) for v in FILES.values())


def test_hf_checksum_mismatch_is_rejected(hub_daemon: Daemon) -> None:
    FakeHub.corrupt = True
    hub_daemon.client.post("/internal/v1/models/local.general.small/install")
    hub_daemon.engine.store.wait("local.general.small", 30)
    files = hub_daemon.client.get("/internal/v1/models/local.general.small").json()["files"]
    assert files["state"] == "DOWNLOAD_ERROR"
    assert files["error"]["code"] == "CHECKSUM_MISMATCH"
    cleared = hub_daemon.client.post(
        "/internal/v1/models/local.general.small/install/cancel", json={"clear": True}
    ).json()
    assert cleared["state"] == "NOT_INSTALLED"
    assert not hub_daemon.engine.store.staging_path(
        hub_daemon.engine.profiles["local.general.small"]
    ).exists()


def test_insufficient_disk_is_reported(hub_daemon: Daemon) -> None:
    hub_daemon.engine.store.disk_reserve_bytes = shutil.disk_usage("/").free * 10
    hub_daemon.client.post("/internal/v1/models/local.general.small/install")
    hub_daemon.engine.store.wait("local.general.small", 30)
    files = hub_daemon.client.get("/internal/v1/models/local.general.small").json()["files"]
    assert files["state"] == "DOWNLOAD_ERROR" and files["error"]["code"] == "INSUFFICIENT_DISK"


def test_refuses_to_start_with_a_short_token(tmp_path: Path) -> None:
    cfg = DaemonConfig(
        socket_path=tmp_path / "s.sock",
        token="",
        token_file=None,
        data_dir=tmp_path / "d",
        model_profiles_dir=ROOT / "catalog/models/dev",
        agent_network="cq-agents-short",
        model_network="cq-models-short",
    )
    with pytest.raises(RuntimeError, match="at least 16"):
        DaemonServer(Engine(cfg), "", None, cfg.socket_path)


def test_rejects_invalid_content_length_and_closes(daemon: Daemon) -> None:
    import socket as _socket

    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(str(daemon.cfg.socket_path))
    sock.sendall(
        f"POST /internal/v1/runs HTTP/1.1\r\nHost: r\r\nAuthorization: Bearer {TOKEN}\r\n"
        "Content-Length: -1\r\n\r\n".encode()
    )
    chunks = []
    while True:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:  # server closed the connection
            break
        chunks.append(chunk)
    data = b"".join(chunks).decode()
    assert "400" in data.split("\r\n")[0] and "INVALID_LENGTH" in data
    sock.close()


def test_unauthorized_body_is_not_parsed_as_a_second_request(daemon: Daemon) -> None:
    import socket as _socket

    smuggled = "GET /internal/v1/health HTTP/1.1\r\nHost: r\r\n\r\n"
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(str(daemon.cfg.socket_path))
    sock.sendall(
        f"POST /internal/v1/runs HTTP/1.1\r\nHost: r\r\nContent-Length: {len(smuggled)}\r\n\r\n{smuggled}".encode()
    )
    chunks = []
    while True:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    reply = b"".join(chunks).decode()
    assert reply.count("HTTP/1.1") == 1 and "401" in reply  # connection closed after the 401
    sock.close()


def test_downloaded_model_files_are_world_readable(daemon: Daemon) -> None:
    import stat as _stat

    daemon.client.post("/internal/v1/models/local.general.small/install")
    daemon.engine.store.wait("local.general.small", 30)
    path = Path(
        daemon.client.get("/internal/v1/models/local.general.small").json()["files"]["path"]
    )
    for item in [path, *path.rglob("*")]:
        mode = item.stat().st_mode
        assert mode & _stat.S_IROTH, item
        if item.is_dir():
            assert mode & _stat.S_IXOTH, item
