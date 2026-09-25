"""Start agent attempts as local processes or hardened Docker containers (spec section 6.3).

The fake platform itself never touches Docker; a launcher runs next to it and reports exits back.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any


@dataclass
class LaunchHandle:
    process: subprocess.Popen[bytes]
    log_path: Path
    log_file: IO[bytes]
    kill_command: list[str] | None = None

    def wait(self, timeout: float | None = None) -> int:
        try:
            return self.process.wait(timeout)
        finally:
            if self.process.returncode is not None:
                self.log_file.close()

    def kill(self) -> None:
        if self.kill_command:
            subprocess.run(self.kill_command, capture_output=True, check=False)
        if self.process.poll() is None:
            self.process.kill()

    def log_text(self) -> str:
        return (
            self.log_path.read_text(encoding="utf-8", errors="replace")
            if self.log_path.exists()
            else ""
        )


def _log_file(log_dir: Path, dispatch: dict[str, Any]) -> tuple[Path, IO[bytes]]:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{dispatch['runId']}-attempt{dispatch['attempt']}.log"
    return path, path.open("wb")


class ProcessLauncher:
    """Runs the manifest entrypoint with the current Python interpreter (no isolation)."""

    def __init__(
        self, entrypoint: list[str], *, agent_dir: Path | None = None, log_dir: Path
    ) -> None:
        self.entrypoint = entrypoint
        self.agent_dir = agent_dir
        self.log_dir = log_dir

    def broker_url_for(self, fake_url: str) -> str:
        return fake_url

    def command(self) -> list[str]:
        command = list(self.entrypoint)
        if command and command[0] in {"python", "python3"}:
            command[0] = sys.executable
        return command

    def start(self, dispatch: dict[str, Any]) -> LaunchHandle:
        env = {**os.environ, **dispatch["env"], "PYTHONUNBUFFERED": "1"}
        if self.agent_dir is not None and (self.agent_dir / "src").is_dir():
            paths = [str(self.agent_dir / "src"), env.get("PYTHONPATH", "")]
            env["PYTHONPATH"] = os.pathsep.join(p for p in paths if p)
        path, log = _log_file(self.log_dir, dispatch)
        process = subprocess.Popen(self.command(), env=env, stdout=log, stderr=subprocess.STDOUT)
        return LaunchHandle(process, path, log)


class DockerLauncher:
    """Runs the pinned image with the runtime hardening from PLAN.md 4.2 and spec section 6.3."""

    def __init__(
        self,
        *,
        log_dir: Path,
        network: str = "crewq-agents",
        broker_url: str = "http://broker:8080",
        docker: str = "docker",
    ) -> None:
        self.log_dir = log_dir
        self.network = network
        self.broker_url = broker_url
        self.docker = docker

    def broker_url_for(self, fake_url: str) -> str:
        return self.broker_url

    @staticmethod
    def container_name(dispatch: dict[str, Any]) -> str:
        return f"crewq-run-{dispatch['runId']}-{dispatch['attempt']}"

    def command(self, dispatch: dict[str, Any]) -> list[str]:
        image = dispatch["image"]
        if "@sha256:" not in image or image.endswith("REQUIRED_DIGEST"):
            raise ValueError(f"refusing to run an image that is not pinned by digest: {image}")
        resources = dispatch["resources"]
        entrypoint = list(dispatch["entrypoint"])
        command = [
            self.docker,
            "run",
            "--rm",
            "--name",
            self.container_name(dispatch),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108 - the container's private tmpfs
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "10001:10001",
            "--pids-limit",
            str(resources.get("pids", 256)),
            "--cpus",
            str(resources["cpu"]),
            "--memory",
            f"{resources['memoryMb']}m",
            "--network",
            self.network,
            "--entrypoint",
            entrypoint[0],
        ]
        for name, value in dispatch["env"].items():
            command += ["-e", f"{name}={value}"]
        return [*command, image, *entrypoint[1:]]

    def start(self, dispatch: dict[str, Any]) -> LaunchHandle:
        path, log = _log_file(self.log_dir, dispatch)
        process = subprocess.Popen(self.command(dispatch), stdout=log, stderr=subprocess.STDOUT)
        kill = [self.docker, "kill", self.container_name(dispatch)]
        return LaunchHandle(process, path, log, kill)
