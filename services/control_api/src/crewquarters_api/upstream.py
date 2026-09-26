"""HTTP clients for the internal services the control API proxies to.

* Capability broker (``CQ_BROKER_URL``): connection status and management, provider keys.
* Knowledge service (``CQ_KNOWLEDGE_URL``): knowledge bases, documents, retrieval.
* Model gateway (``CQ_MODEL_GATEWAY_URL``): provider-key tests.

Every call carries ``Authorization: Bearer $CQ_INTERNAL_SERVICE_TOKEN``. Upstream platform
errors keep their code and status, so the UI sees the owning service's error codes. Two
cases are translated: an unreachable service becomes ``503 <SERVICE>_UNAVAILABLE``, and an
upstream ``401`` (a service-token mismatch, never the browser's session) becomes
``502 UPSTREAM_AUTH_FAILED`` so the UI does not sign the owner out.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, BinaryIO

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared.errors import PlatformError, not_found

PROVIDERS = {
    "google": "Google",
    "twilio": "Twilio",
    "github": "GitHub",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
}


class ServiceClient:
    """One internal service's ``/internal/v1`` API."""

    def __init__(
        self,
        name: str,
        base_url: str,
        service_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self._unavailable = f"{name.upper().replace('-', '_')}_UNAVAILABLE"
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/internal/v1",
            headers={"authorization": f"Bearer {service_token}"},
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: dict[str, tuple[str, BinaryIO, str]] | None = None,
        read_timeout: float | None = None,
    ) -> Any:
        extra: dict[str, Any] = {}
        if read_timeout is not None:
            extra["timeout"] = httpx.Timeout(read_timeout, connect=5.0)
        try:
            response = await self._http.request(
                method, path, json=json, params=params, files=files, **extra
            )
        except httpx.HTTPError as exc:
            raise PlatformError(
                self._unavailable,
                f"The {self.name} service is unavailable ({type(exc).__name__}).",
                503,
            ) from exc
        return self._unwrap(response)

    def _unwrap(self, response: httpx.Response) -> Any:
        if response.status_code < 400:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        try:
            error = response.json()["error"]
            code, message = str(error["code"]), str(error["message"])
            details = error.get("details") or {}
        except (ValueError, KeyError, TypeError):
            code, message, details = "UPSTREAM_ERROR", f"The {self.name} request failed.", {}
        if response.status_code == 401:
            raise PlatformError(
                "UPSTREAM_AUTH_FAILED",
                f"The control API could not authenticate to the {self.name} service.",
                502,
            )
        status = response.status_code if response.status_code < 500 else 502
        if response.status_code == 503:
            status = 503
        raise PlatformError(code, message, status, details)

    async def close(self) -> None:
        await self._http.aclose()


class BrokerClient(ServiceClient):
    """Capability broker. Also the real ``ConnectionStatusClient``: when the broker cannot be
    reached every provider reports ``UNKNOWN``, never ``CONNECTED``."""

    STATUS_CACHE_SECONDS = 2.0

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__("broker", base_url, service_token, transport=transport)
        self._cached: tuple[float, list[dict[str, Any]]] | None = None

    async def list_connections(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._cached is not None and now - self._cached[0] < self.STATUS_CACHE_SECONDS:
            return [dict(c) for c in self._cached[1]]
        try:
            found = await self.request("GET", "/connections", read_timeout=5.0)
        except PlatformError as exc:
            return unknown_connections(exc.code)
        connections = [dict(c) for c in found if c.get("provider") in PROVIDERS]
        self._cached = (now, connections)
        return [dict(c) for c in connections]

    def invalidate(self) -> None:
        """Forget cached status after a change made through the control API."""
        self._cached = None


def unknown_connections(reason: str) -> list[dict[str, Any]]:
    return [
        {
            "provider": provider,
            "displayName": name,
            "status": "UNKNOWN",
            "grantedCapabilities": [],
            "lastCheckedAt": None,
            "detail": f"Connection status is unavailable ({reason}).",
        }
        for provider, name in PROVIDERS.items()
    ]


# --- Knowledge-base ownership -------------------------------------------------------------
#
# The knowledge service trusts its callers to authorize the knowledge base (docs/knowledge.md)
# and its views carry no owner. The control API therefore reads the one ownership column it
# needs, ``knowledge_bases.owner_id``, read-only. It never reads documents or chunks.


async def knowledge_base_owner(db: AsyncSession, kb_id: uuid.UUID) -> uuid.UUID | None:
    owner: uuid.UUID | None = await db.scalar(
        text("SELECT owner_id FROM knowledge_bases WHERE id = :id"), {"id": kb_id}
    )
    return owner


async def owned_knowledge_base_ids(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = await db.scalars(
        text("SELECT id FROM knowledge_bases WHERE owner_id = :owner"), {"owner": user_id}
    )
    return {str(r) for r in rows.all()}


async def require_knowledge_base(db: AsyncSession, kb_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """404 (never 403) for a knowledge base that is missing or someone else's."""
    if await knowledge_base_owner(db, kb_id) != user_id:
        raise not_found("Knowledge base", kb_id)
