"""``crewquarters-runtime``: run the daemon or check the host.

crewquarters-runtime serve            run the daemon (systemd socket or CQ_RUNTIME_SOCKET)
crewquarters-runtime preflight [--appliance] [--gpu-test]
                                      check architecture, Docker, NVIDIA runtime, memory, disk
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import subprocess
import sys
import threading
from typing import Any

from crewquarters_runtime import probe
from crewquarters_runtime.config import DaemonConfig
from crewquarters_runtime.docker_api import DockerClient
from crewquarters_runtime.engine import Engine
from crewquarters_runtime.server import DaemonServer, systemd_socket

MIN_APPLIANCE_MEMORY = 120 * 1024**3
MIN_FREE_DISK = 100 * 1024**3
GPU_TEST_IMAGE = "nvcr.io/nvidia/cuda:12.9.1-base-ubuntu24.04"


def serve(cfg: DaemonConfig) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"runtime-daemon","message":"%(message)s"}',
    )
    engine = Engine(cfg)
    engine.reconcile()
    server = DaemonServer(engine, cfg.service_token(), systemd_socket(), cfg.socket_path)
    stop = threading.Event()

    def housekeeping() -> None:
        while not stop.wait(60):
            try:
                engine.reconcile()
            except Exception:
                logging.exception("housekeeping failed")

    threading.Thread(target=housekeeping, daemon=True).start()

    def shutdown(*_: Any) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logging.info("runtime daemon listening on %s", cfg.socket_path)
    server.serve_forever()
    server.server_close()
    return 0


def preflight(
    cfg: DaemonConfig, appliance: bool, gpu_test: bool
) -> tuple[bool, list[dict[str, Any]]]:
    """Checks used by the .deb postinst and the setup wizard (PLAN.md sections 13.4, 14.2)."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        status = "passed" if ok else ("failed" if required else "warning")
        checks.append({"name": name, "status": status, "detail": detail})

    arch = probe.architecture()
    add("architecture", arch == "linux/arm64" or not appliance, arch, required=appliance)
    os_info = probe._os_release()
    add(
        "operating system",
        os_info.get("id") == "ubuntu",
        os_info.get("name", "unknown"),
        required=False,
    )
    docker = probe.docker_status(DockerClient(cfg.docker_socket))
    add("docker", docker["available"], docker.get("version") or docker.get("detail", ""))
    compose = shutil.which("docker") is not None and _compose_available()
    add("docker compose", compose, "docker compose plugin" if compose else "missing")
    add(
        "nvidia container runtime",
        bool(docker.get("nvidiaRuntime") or docker.get("cdi")),
        docker.get("detail", ""),
        required=appliance,
    )
    gpu = probe.gpu()
    add("gpu", gpu["available"], gpu.get("name") or gpu.get("detail", ""), required=appliance)
    memory = probe.meminfo()["totalBytes"]
    add(
        "memory",
        memory >= MIN_APPLIANCE_MEMORY,
        f"{memory / 1024**3:.0f} GiB (128 GB class expected)",
        required=False,
    )
    free = probe.disk(cfg.data_dir)["freeBytes"]
    add("disk", free >= MIN_FREE_DISK, f"{free / 1024**3:.0f} GiB free", required=False)
    if gpu_test:
        ok, detail = _gpu_container_test()
        add("gpu container test", ok, detail, required=appliance)
    passed = all(c["status"] != "failed" for c in checks)
    return passed, checks


def _compose_available() -> bool:
    try:
        subprocess.run(
            ["docker", "compose", "version"],  # noqa: S607 - docker from PATH
            capture_output=True,
            check=True,
            timeout=20,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def _gpu_container_test() -> tuple[bool, str]:
    """The installer's minimal ``docker run --gpus=all ... nvidia-smi`` check."""
    try:
        out = subprocess.run(  # noqa: S603
            ["docker", "run", "--rm", "--gpus=all", GPU_TEST_IMAGE, "nvidia-smi", "-L"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
        ).stdout.strip()
        return True, out.splitlines()[0] if out else "ok"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"GPU container test failed: {type(exc).__name__}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crewquarters-runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the runtime daemon")
    pre = sub.add_parser("preflight", help="Check host prerequisites")
    pre.add_argument("--appliance", action="store_true", help="Require arm64 + NVIDIA runtime")
    pre.add_argument("--gpu-test", action="store_true", help="Run a GPU container test")
    pre.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    cfg = DaemonConfig()
    if args.command == "serve":
        return serve(cfg)
    passed, checks = preflight(cfg, args.appliance, args.gpu_test)
    if args.json:
        print(json.dumps({"passed": passed, "checks": checks}, indent=2))
    else:
        for check in checks:
            print(f"[{check['status']:7}] {check['name']}: {check['detail']}")
        print("preflight passed" if passed else "preflight FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
