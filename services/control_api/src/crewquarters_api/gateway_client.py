"""HTTP client for the model gateway (replaces the fake model-status client).

The control API never talks to model servers or providers directly: model status and
actions, chat leases, and chat inference all go through the gateway's internal API.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from crewquarters_shared.errors import PlatformError


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 900.0,
        chat_token: str | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"}
        if chat_token:
            headers["X-Chat-Client-Token"] = chat_token
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
        )

    async def _call(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> Any:
        headers = {"X-Actor-Id": actor} if actor else None
        try:
            response = await self._client.request(method, path, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise PlatformError(
                "MODEL_GATEWAY_UNAVAILABLE", f"Model gateway unreachable: {type(exc).__name__}", 503
            ) from exc
        return _unwrap(response)

    # --- ModelStatusClient protocol ---------------------------------------------------

    async def list_models(self) -> list[dict[str, Any]]:
        return list(await self._call("GET", "/internal/v1/models"))

    async def get_model(self, model_id: str) -> dict[str, Any] | None:
        try:
            return dict(await self._call("GET", f"/internal/v1/models/{model_id}"))
        except PlatformError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def request_action(
        self, model_id: str, action: str, *, force: bool = False, actor: str | None = None
    ) -> dict[str, Any]:
        if action == "delete":
            return dict(
                await self._call("DELETE", f"/internal/v1/models/{model_id}/files", actor=actor)
            )
        if action == "cancel_install":
            return dict(
                await self._call(
                    "POST",
                    f"/internal/v1/models/{model_id}/install/cancel",
                    {"clear": force},
                    actor,
                )
            )
        body = {"force": force} if action == "unload" else None
        return dict(
            await self._call("POST", f"/internal/v1/models/{model_id}/{action}", body, actor)
        )

    async def memory(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/internal/v1/memory"))

    async def model_events(self, model_id: str) -> AsyncIterator[str]:
        """Relay the gateway's model SSE stream line by line."""
        try:
            async with self._client.stream(
                "GET", f"/internal/v1/models/{model_id}/events"
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    _unwrap(response)
                async for line in response.aiter_lines():
                    yield line
        except httpx.HTTPError as exc:
            raise PlatformError(
                "MODEL_GATEWAY_UNAVAILABLE", "Model gateway stream failed.", 503
            ) from exc

    # --- chat ------------------------------------------------------------------------------

    async def acquire_chat_lease(
        self, model_id: str, session_id: str, label: str
    ) -> dict[str, Any]:
        return dict(
            await self._call(
                "POST",
                "/internal/v1/leases",
                {"modelId": model_id, "holderType": "chat", "holderId": session_id, "label": label},
            )
        )

    async def release_chat_lease(self, session_id: str) -> None:
        await self._call("DELETE", f"/internal/v1/leases/holders/chat/{session_id}")

    async def chat_stream(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yield gateway stream events: {"type": "delta"|"done"|"error", ...}."""
        try:
            async with self._client.stream(
                "POST", "/internal/v1/llm/chat", json={**body, "stream": True}
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    _unwrap(response)
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        yield json.loads(line[6:])
        except httpx.HTTPError as exc:
            raise PlatformError(
                "MODEL_GATEWAY_UNAVAILABLE", "Model gateway stream failed.", 503
            ) from exc

    async def health(self) -> bool:
        try:
            response = await self._client.get("/internal/v1/health", timeout=3)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()


def _unwrap(response: httpx.Response) -> Any:
    if response.status_code < 400:
        return response.json() if response.content else {}
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    raise PlatformError(
        error.get("code", "MODEL_GATEWAY_ERROR"),
        error.get("message", "Model gateway error."),
        response.status_code if response.status_code < 500 else 502,
        error.get("details") or {},
    )
