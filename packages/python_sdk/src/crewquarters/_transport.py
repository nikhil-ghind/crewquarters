"""HTTP transport to the capability broker with bounded, idempotency-aware retries (spec 5.4)."""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from crewquarters.errors import OutcomeUnknown, PlatformError, ProviderError, error_from_response

SDK_PREFIX = "/internal/v1/sdk"
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
_DEFAULT_TIMEOUT = 30.0
_CONNECT_TIMEOUT = 5.0


def _default_jitter() -> float:
    return random.uniform(0.0, 0.25)  # noqa: S311 - retry jitter, not a secret


class BrokerClient:
    """Sends broker requests. Only idempotent operations are retried after they may have run."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_attempts: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = _default_jitter,
    ) -> None:
        self._base = base_url.rstrip("/") + SDK_PREFIX
        self._token = token
        self._http = http or httpx.AsyncClient()
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._jitter = jitter

    def _headers(self, request_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "X-Request-Id": request_id}

    def _backoff(self, attempt: int) -> float:
        return float(min(8.0, 0.5 * 2 ** (attempt - 1)) + self._jitter())

    @staticmethod
    def _timeout(seconds: float | None) -> httpx.Timeout:
        return httpx.Timeout(seconds or _DEFAULT_TIMEOUT, connect=_CONNECT_TIMEOUT)

    async def request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        idempotent: bool,
        json: Any = None,
        params: dict[str, Any] | None = None,
        read_timeout: float | None = None,
    ) -> Any:
        attempt = 0
        while True:
            attempt += 1
            request_id = str(uuid.uuid4())
            try:
                response = await self._http.request(
                    method,
                    self._base + path,
                    json=json,
                    params=params,
                    headers=self._headers(request_id),
                    timeout=self._timeout(read_timeout),
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # The request never reached the broker, so retrying cannot duplicate an effect.
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff(attempt))
                    continue
                raise PlatformError(
                    f"broker unreachable during {operation}: {exc}",
                    code="BROKER_UNAVAILABLE",
                    request_id=request_id,
                    retryable=True,
                ) from exc
            except httpx.TransportError as exc:
                if idempotent and attempt < self._max_attempts:
                    await self._sleep(self._backoff(attempt))
                    continue
                if idempotent:
                    raise PlatformError(
                        f"broker connection failed during {operation}: {exc}",
                        code="BROKER_UNAVAILABLE",
                        request_id=request_id,
                        retryable=True,
                    ) from exc
                raise OutcomeUnknown(
                    f"{operation} may or may not have been applied: {exc}", request_id=request_id
                ) from exc

            if response.status_code < 400:
                return response.json() if response.content else None

            error = error_from_response(response.status_code, _safe_json(response), request_id)
            retry_status = (
                response.status_code in _RETRYABLE_STATUSES and error.code != "MODEL_UNAVAILABLE"
            )
            if idempotent and retry_status and attempt < self._max_attempts:
                await self._sleep(_retry_after(response) or self._backoff(attempt))
                continue
            if not idempotent and response.status_code == 504:
                raise OutcomeUnknown(
                    f"{operation} timed out upstream; it may or may not have been applied",
                    request_id=request_id,
                )
            raise error

    async def stream_sse(
        self, path: str, *, operation: str, json: Any, read_timeout: float | None = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """POST and yield ``(event, data)`` pairs from a server-sent-events stream; no retries."""
        request_id = str(uuid.uuid4())
        try:
            async with self._http.stream(
                "POST",
                self._base + path,
                json=json,
                headers=self._headers(request_id),
                timeout=self._timeout(read_timeout),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise error_from_response(
                        response.status_code, _safe_json(response), request_id
                    )
                event = "message"
                data: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        if data:
                            yield event, _parse_data(data)
                        event, data = "message", []
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    value = value.removeprefix(" ")
                    if field == "event":
                        event = value
                    elif field == "data":
                        data.append(value)
                if data:
                    yield event, _parse_data(data)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise PlatformError(
                f"broker unreachable during {operation}: {exc}",
                code="BROKER_UNAVAILABLE",
                request_id=request_id,
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                f"{operation} stream interrupted: {exc}",
                code="STREAM_INTERRUPTED",
                request_id=request_id,
            ) from exc

    async def aclose(self) -> None:
        await self._http.aclose()


def _parse_data(lines: list[str]) -> dict[str, Any]:
    parsed = json.loads("\n".join(lines))
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _safe_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
