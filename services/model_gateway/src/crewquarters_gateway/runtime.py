"""Access to model-serving runtimes.

``DaemonModelRuntime`` talks to the host runtime daemon (the only component with
Docker access). ``InProcessModelRuntime`` is a deterministic stand-in for unit tests
that keeps the same install/start/stop state machine without containers.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Protocol

import httpx

from crewquarters_shared.errors import PlatformError

PULL_TIMEOUT_SECONDS = 3600.0


class ModelRuntime(Protocol):
    async def model_state(self, model_id: str) -> dict[str, Any]: ...

    async def install(self, model_id: str) -> dict[str, Any]: ...

    async def cancel_install(self, model_id: str, clear: bool) -> dict[str, Any]: ...

    async def prepare(self, model_id: str) -> dict[str, Any]: ...

    async def delete_files(self, model_id: str) -> dict[str, Any]: ...

    async def start(self, model_id: str) -> dict[str, Any]: ...

    async def stop(self, model_id: str) -> dict[str, Any]: ...

    async def capacity(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class DaemonModelRuntime:
    def __init__(self, socket_path: Path, token: str, timeout: float = 120.0) -> None:
        self._client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
            base_url="http://runtime-daemon",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    async def _call(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        limit_seconds: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, json=json, timeout=limit_seconds or self._client.timeout
            )
        except httpx.HTTPError as exc:
            raise PlatformError(
                "RUNTIME_UNAVAILABLE", f"Runtime daemon unreachable: {type(exc).__name__}", 503
            ) from exc
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            status = (
                409
                if response.status_code == 409
                else (404 if response.status_code == 404 else 502)
            )
            raise PlatformError(
                error.get("code", "RUNTIME_ERROR"),
                error.get("message", "Runtime daemon error."),
                status,
            )
        return dict(body)

    async def model_state(self, model_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/internal/v1/models/{model_id}")

    async def install(self, model_id: str) -> dict[str, Any]:
        return await self._call("POST", f"/internal/v1/models/{model_id}/install")

    async def cancel_install(self, model_id: str, clear: bool) -> dict[str, Any]:
        return await self._call(
            "POST", f"/internal/v1/models/{model_id}/install/cancel", {"clear": clear}
        )

    async def delete_files(self, model_id: str) -> dict[str, Any]:
        return await self._call("DELETE", f"/internal/v1/models/{model_id}/files")

    async def prepare(self, model_id: str) -> dict[str, Any]:
        """Pull the serving image (can take many minutes for vLLM)."""
        return await self._call(
            "POST", f"/internal/v1/models/{model_id}/prepare", limit_seconds=PULL_TIMEOUT_SECONDS
        )

    async def start(self, model_id: str) -> dict[str, Any]:
        return await self._call("POST", f"/internal/v1/models/{model_id}/start")

    async def stop(self, model_id: str) -> dict[str, Any]:
        return await self._call(
            "POST", f"/internal/v1/models/{model_id}/stop", {"graceSeconds": 30}
        )

    async def capacity(self) -> dict[str, Any]:
        return await self._call("GET", "/internal/v1/host/capacity")

    async def close(self) -> None:
        await self._client.aclose()


class InProcessModelRuntime:
    """No containers: installs instantly and serves the in-process mock backend."""

    def __init__(self, available_bytes: int = 64 * 1024**3) -> None:
        self.available_bytes = available_bytes
        self.files: dict[str, str] = {}
        self.running: set[str] = set()
        self.fail_start: set[str] = set()
        self.crash: set[str] = set()
        self.starts: list[str] = []
        self.stops: list[str] = []

    async def model_state(self, model_id: str) -> dict[str, Any]:
        running = model_id in self.running and model_id not in self.crash
        container: dict[str, Any] = {"state": "absent"}
        if model_id in self.running:
            container = {
                "state": "running" if running else "exited",
                "exitCode": None if running else 137,
                "oomKilled": model_id in self.crash,
                "endpoint": {"inprocess": True, "servedModelName": model_id},
            }
        return {
            "modelId": model_id,
            "files": {
                "state": self.files.get(model_id, "NOT_INSTALLED"),
                "bytesDone": 0,
                "bytesTotal": 0,
            },
            "container": container,
        }

    async def install(self, model_id: str) -> dict[str, Any]:
        self.files[model_id] = "INSTALLED"
        return dict((await self.model_state(model_id))["files"])

    async def cancel_install(self, model_id: str, clear: bool) -> dict[str, Any]:
        if clear:
            self.files.pop(model_id, None)
        return dict((await self.model_state(model_id))["files"])

    async def prepare(self, model_id: str) -> dict[str, Any]:
        return {"modelId": model_id, "status": "present"}

    async def delete_files(self, model_id: str) -> dict[str, Any]:
        if model_id in self.running:
            raise PlatformError("MODEL_RESIDENT", "Stop the model before deleting its files.", 409)
        self.files.pop(model_id, None)
        return dict((await self.model_state(model_id))["files"])

    async def start(self, model_id: str) -> dict[str, Any]:
        if model_id in self.fail_start:
            raise PlatformError("DOCKER_ERROR", "simulated start failure", 502)
        self.starts.append(model_id)
        self.running.add(model_id)
        return await self.model_state(model_id)

    async def stop(self, model_id: str) -> dict[str, Any]:
        self.stops.append(model_id)
        self.running.discard(model_id)
        self.crash.discard(model_id)
        return {"modelId": model_id, "containerRemoved": True}

    async def capacity(self) -> dict[str, Any]:
        return {
            "architecture": platform.machine(),
            "memory": {"totalBytes": 128 * 1024**3, "availableBytes": self.available_bytes},
            "gpu": {"available": False},
        }

    async def close(self) -> None:
        return None
