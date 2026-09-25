"""Client for the control API's internal run endpoints (run state checks and the
``LOADING_MODEL`` signal while a run waits for a model to load)."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

import httpx

from crewquarters_shared.errors import PlatformError

log = logging.getLogger("crewquarters.gateway.control")
ACTIVE_RUN_STATES = {"PREPARING", "LOADING_MODEL", "RUNNING", "WAITING_INPUT"}


class RunDirectory(Protocol):
    async def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    async def set_model_loading(
        self, run_id: str, attempt: int, loading: bool, model: str | None
    ) -> None: ...


class ControlApiClient:
    def __init__(
        self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            transport=transport,
        )
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

    async def get_run(self, run_id: str, max_age: float = 0.0) -> dict[str, Any] | None:
        cached = self._cache.get(run_id)
        if cached and time.monotonic() - cached[0] <= max_age:
            return cached[1]
        try:
            response = await self._client.get(f"/internal/v1/runs/{run_id}")
        except httpx.HTTPError as exc:
            raise PlatformError(
                "CONTROL_API_UNAVAILABLE", f"Control API unreachable: {type(exc).__name__}", 503
            ) from exc
        run = None if response.status_code == 404 else response.raise_for_status().json()
        self._cache[run_id] = (time.monotonic(), run)
        return run

    async def run_is_active(self, run_id: str) -> bool:
        try:
            run = await self.get_run(run_id)
        except PlatformError:
            return True  # never release leases because the control plane is briefly down
        return run is not None and run["state"] in ACTIVE_RUN_STATES

    async def set_model_loading(
        self, run_id: str, attempt: int, loading: bool, model: str | None
    ) -> None:
        try:
            await self._client.post(
                f"/internal/v1/runs/{run_id}/model-state",
                json={"attempt": attempt, "loading": loading, "model": model},
            )
        except httpx.HTTPError:
            log.warning("could not signal model-state to the control API", extra={"run_id": run_id})

    async def close(self) -> None:
        await self._client.aclose()
