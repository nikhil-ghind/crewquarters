"""Host capability probe: architecture, memory, disk, Docker, NVIDIA runtime, GPU."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from crewquarters_runtime.docker_api import DockerClient, DockerError

_ARCH = {
    "x86_64": "linux/amd64",
    "amd64": "linux/amd64",
    "aarch64": "linux/arm64",
    "arm64": "linux/arm64",
}


def architecture() -> str:
    machine = platform.machine().lower()
    return _ARCH.get(machine, f"linux/{machine}")


def meminfo() -> dict[str, int]:
    """Bytes from /proc/meminfo (MemTotal, MemAvailable)."""
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
    except OSError:
        pass
    return {
        "totalBytes": values.get("MemTotal", 0),
        "availableBytes": values.get("MemAvailable", 0),
    }


def disk(path: Path) -> dict[str, int]:
    target = path if path.exists() else Path("/")
    usage = shutil.disk_usage(target)
    return {"totalBytes": usage.total, "freeBytes": usage.free}


def gpu() -> dict[str, Any]:
    """Query nvidia-smi if present. On unified-memory GB10 the GPU memory figure is a
    view of the same 128 GB pool, so admission control uses host memory as well."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return {"available": False, "detail": "nvidia-smi not found"}
    try:
        out = subprocess.run(  # noqa: S603 - fixed binary and arguments
            [
                exe,
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return {"available": False, "detail": f"nvidia-smi failed: {type(exc).__name__}"}
    devices = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:

            def _mib(value: str) -> int | None:
                return int(value) * 1024 * 1024 if value.isdigit() else None

            devices.append(
                {
                    "name": parts[0],
                    "memoryTotalBytes": _mib(parts[1]),
                    "memoryUsedBytes": _mib(parts[2]),
                    "driver": parts[3],
                }
            )
    return {
        "available": bool(devices),
        "name": devices[0]["name"] if devices else None,
        "devices": devices,
    }


def docker_status(client: DockerClient) -> dict[str, Any]:
    try:
        info = client.info()
        version = client.version()
    except (DockerError, OSError) as exc:
        return {"available": False, "detail": f"Docker unreachable: {exc}"}
    runtimes = sorted((info.get("Runtimes") or {}).keys())
    # CDI counts only if an NVIDIA device spec is actually present.
    cdi = any(
        any(Path(d).glob("nvidia*")) for d in (info.get("CDISpecDirs") or []) if Path(d).is_dir()
    )
    nvidia = "nvidia" in runtimes
    return {
        "available": True,
        "version": version.get("Version"),
        "apiVersion": version.get("ApiVersion"),
        "runtimes": runtimes,
        "nvidiaRuntime": nvidia,
        "cdi": cdi,
        "detail": "NVIDIA container runtime configured"
        if nvidia
        else "NVIDIA container runtime not configured",
    }


def capacity(client: DockerClient, data_dir: Path) -> dict[str, Any]:
    return {
        "architecture": architecture(),
        "cpuCount": os.cpu_count(),
        "memory": meminfo(),
        "disk": disk(data_dir),
        "docker": docker_status(client),
        "gpu": gpu(),
        "os": _os_release(),
    }


def _os_release() -> dict[str, str]:
    try:
        pairs = (
            line.split("=", 1)
            for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
        data = {k: v.strip('"') for k, v in pairs}
        return {
            "id": data.get("ID", ""),
            "version": data.get("VERSION_ID", ""),
            "name": data.get("PRETTY_NAME", ""),
        }
    except OSError:
        return {}
