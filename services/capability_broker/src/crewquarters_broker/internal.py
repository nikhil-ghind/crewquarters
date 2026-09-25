"""Client for another service's ``/internal/v1`` routes: the control API's run routes
(docs/control-plane.md) and the knowledge service's query route."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from crewquarters_shared.errors import PlatformError

# Input long-polls wait up to 30 s on the control API.
TIMEOUT_SECONDS = 35.0


class InternalClient:
    def __init__(
        self, base_url: str, service_token: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/internal/v1",
            headers={"authorization": f"Bearer {service_token}"},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call a route; platform errors are re-raised with their code and status."""
        resp = await self._http.request(method, path, json=json, params=params)
        if resp.status_code < 400:
            return resp.json() if resp.content else None
        try:
            err = resp.json()["error"]
            code, message, details = err["code"], err["message"], err.get("details") or {}
        except (ValueError, KeyError, TypeError):
            raise PlatformError("UPSTREAM_ERROR", "Internal service request failed.", 502) from None
        raise PlatformError(code, message, resp.status_code, details)

    async def get_run(self, run_id: uuid.UUID | str) -> dict[str, Any]:
        run: dict[str, Any] = await self.request("GET", f"/runs/{run_id}")
        return run

    async def close(self) -> None:
        await self._http.aclose()
