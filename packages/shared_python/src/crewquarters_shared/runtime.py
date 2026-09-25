"""Runtime adapter contract between the worker and the runtime daemon.

The real implementation talks to the host runtime daemon (Person 2) over its Unix
socket (PLAN.md section 5.2). ``start_run`` MUST be idempotent on
``(run_id, attempt)``: a worker that crashes after starting a container and is
replaced by another worker must receive the same runtime reference, never a
second container.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class RunSpec:
    run_id: uuid.UUID
    attempt: int
    installation_id: uuid.UUID
    agent_version_id: uuid.UUID
    image: str
    entrypoint: list[str]
    architectures: list[str]
    cpu: float
    memory_mb: int
    pids: int
    env: dict[str, str]
    config: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        data = asdict(self)
        data["run_id"] = str(self.run_id)
        data["installation_id"] = str(self.installation_id)
        data["agent_version_id"] = str(self.agent_version_id)
        return data


@dataclass(frozen=True)
class RuntimeStatus:
    state: str  # starting | running | exited | missing
    exit_code: int | None = None
    oom_killed: bool = False
    finished_at: str | None = None
    memory_limit_bytes: int | None = None


class RuntimeAdapter(Protocol):
    async def start_run(self, spec: RunSpec) -> str: ...

    async def get_run(self, runtime_ref: str) -> RuntimeStatus: ...

    async def stop_run(self, runtime_ref: str, grace_seconds: int) -> None: ...

    async def capacity(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class DaemonRuntimeClient:
    """HTTP-over-Unix-socket client for the runtime daemon's internal API."""

    def __init__(self, socket_path: Path, service_token: str, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
            base_url="http://runtime-daemon",
            headers={"Authorization": f"Bearer {service_token}"},
            timeout=timeout,
        )

    async def start_run(self, spec: RunSpec) -> str:
        response = await self._client.post("/internal/v1/runs", json=spec.to_wire())
        response.raise_for_status()
        return str(response.json()["runtimeRef"])

    async def get_run(self, runtime_ref: str) -> RuntimeStatus:
        response = await self._client.get(f"/internal/v1/runs/{runtime_ref}")
        if response.status_code == 404:
            return RuntimeStatus("missing")
        response.raise_for_status()
        body = response.json()
        return RuntimeStatus(
            state=body["state"],
            exit_code=body.get("exitCode"),
            oom_killed=bool(body.get("oomKilled")),
            finished_at=body.get("finishedAt"),
            memory_limit_bytes=body.get("memoryLimitBytes"),
        )

    async def stop_run(self, runtime_ref: str, grace_seconds: int) -> None:
        response = await self._client.post(
            f"/internal/v1/runs/{runtime_ref}/cancel", json={"graceSeconds": grace_seconds}
        )
        if response.status_code != 404:
            response.raise_for_status()

    async def capacity(self) -> dict[str, Any]:
        response = await self._client.get("/internal/v1/host/capacity")
        response.raise_for_status()
        return dict(response.json())

    async def close(self) -> None:
        await self._client.aclose()
