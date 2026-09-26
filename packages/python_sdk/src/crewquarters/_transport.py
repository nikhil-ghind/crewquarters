"""HTTP transport to the capability broker with bounded, idempotency-aware retries (spec 5.4)."""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from crewquarters.errors import OutcomeUnknown, PlatformError, ProviderError, error_from_response

SDK_PREFIX = "/internal/v1/sdk"
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
_DEFAULT_TIMEOUT = 30.0
_CONNECT_TIMEOUT = 5.0
# Outage budget until the handshake tells the SDK the heartbeat interval: most of the
# platform's default 30 s heartbeat timeout (``CQ_HEARTBEAT_TIMEOUT_SECONDS``).
DEFAULT_OUTAGE_BUDGET = 25.0
_OUTAGE_BACKOFF_CAP = 4.0
# The request never reached the broker: retrying cannot duplicate an effect.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# The connection broke after the request may have been sent (for example a broker restart).
_CONNECTION_LOST = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)
# 502/503 codes that mean the platform itself (broker -> control API) is unavailable, as
# opposed to a provider or a model being unavailable.
_PLATFORM_UNAVAILABLE_CODES = frozenset({"UPSTREAM_ERROR", "BROKER_UNAVAILABLE"})


def _default_jitter() -> float:
    return random.uniform(0.0, 0.25)  # noqa: S311 - retry jitter, not a secret


def outage_budget(heartbeat_interval_seconds: float) -> float:
    """How long to ride out a broker outage, given the handshake's heartbeat interval.

    The broker sends ``heartbeatIntervalSeconds`` = heartbeat timeout / 3. The lease was last
    extended at most one interval before an outage began, so it lapses 2 to 3 intervals into
    it; after that the attempt is ``INTERRUPTED`` and retrying is pointless. 2.5 intervals is
    25 s with the default 30 s timeout. Clamped to 5-120 s.
    """
    return min(120.0, max(5.0, 2.5 * heartbeat_interval_seconds))


class BrokerClient:
    """Sends broker requests. Only idempotent operations are retried after they may have run.

    Two retry policies apply:

    * **Outages** (the broker or the control API is restarting). A connection that could not
      be opened is retried for every operation, because nothing was sent. A connection that
      broke mid-request, and a 502/503 meaning the platform itself is unavailable, are retried
      for idempotent operations only. These retries use capped exponential backoff with jitter
      and continue until ``outage_budget`` seconds have passed since the first failure.
    * **Everything else retryable** (429, 502/503/504 from providers, read timeouts):
      idempotent operations only, at most ``max_attempts`` attempts.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_attempts: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = _default_jitter,
        outage_budget: float = DEFAULT_OUTAGE_BUDGET,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/") + SDK_PREFIX
        self._token = token
        self._http = http or httpx.AsyncClient()
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._jitter = jitter
        self._clock = clock
        self.outage_budget = outage_budget

    @property
    def base_url(self) -> str:
        """The broker's SDK API base (``…/internal/v1/sdk``)."""
        return self._base

    @property
    def token(self) -> str:
        """The run token (the credential for OpenAI-compatible model calls)."""
        return self._token

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
        headers: dict[str, str] | None = None,
    ) -> Any:
        """``headers`` are sent unchanged on every retry (for example ``X-Claim-Token``)."""
        attempt = 0  # attempts counted against max_attempts
        outages = 0  # outage retries so far, for their backoff
        outage_began: float | None = None
        slept = 0.0

        async def outage_retry(retry_after: float | None = None) -> bool:
            """Sleep before the next outage retry; False once the budget is spent.

            The budget runs from the first failure, not from the start of the call: a long
            poll that has waited 20 s when the broker restarts still gets the whole budget.
            """
            nonlocal outages, outage_began, slept
            if outage_began is None:
                outage_began = self._clock()
            # max() keeps an injected no-op sleep (tests) from retrying forever.
            remaining = self.outage_budget - max(self._clock() - outage_began, slept)
            if remaining <= 0:
                return False
            outages += 1
            delay = retry_after
            if delay is None:
                delay = min(_OUTAGE_BACKOFF_CAP, 0.5 * 2 ** (outages - 1)) + self._jitter()
            delay = min(delay, remaining)
            slept += delay
            await self._sleep(delay)
            return True

        while True:
            request_id = str(uuid.uuid4())
            try:
                response = await self._http.request(
                    method,
                    self._base + path,
                    json=json,
                    params=params,
                    headers={**(headers or {}), **self._headers(request_id)},
                    timeout=self._timeout(read_timeout),
                )
            except _NOT_SENT as exc:
                if await outage_retry():
                    continue
                raise PlatformError(
                    f"broker unreachable during {operation}: {exc}",
                    code="BROKER_UNAVAILABLE",
                    request_id=request_id,
                    retryable=True,
                ) from exc
            except httpx.TransportError as exc:
                if not idempotent:
                    raise OutcomeUnknown(
                        f"{operation} may or may not have been applied: {exc}",
                        request_id=request_id,
                    ) from exc
                if isinstance(exc, _CONNECTION_LOST):
                    if await outage_retry():
                        continue
                else:  # a timeout: the broker is up but slow
                    attempt += 1
                    if attempt < self._max_attempts:
                        await self._sleep(self._backoff(attempt))
                        continue
                raise PlatformError(
                    f"broker connection failed during {operation}: {exc}",
                    code="BROKER_UNAVAILABLE",
                    request_id=request_id,
                    retryable=True,
                ) from exc

            if response.status_code < 400:
                return response.json() if response.content else None

            body = _safe_json(response)
            error = error_from_response(response.status_code, body, request_id)
            if idempotent and _platform_unavailable(response.status_code, body):
                if await outage_retry(_retry_after(response)):
                    continue
                raise error
            retry_status = (
                response.status_code in _RETRYABLE_STATUSES and error.code != "MODEL_UNAVAILABLE"
            )
            attempt += 1
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


def _platform_unavailable(status: int, body: object) -> bool:
    """A 502/503 from the platform itself: the broker's upstream (the control API) failed, or
    the response is not a broker error at all."""
    if status not in (502, 503):
        return False
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return not isinstance(code, str) or code in _PLATFORM_UNAVAILABLE_CODES


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
