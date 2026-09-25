"""Structured run events (log, progress, metric, artifact), buffered and delivered in batches.

Payloads follow ``packages/contracts/events/run-event.schema.json``: agents emit ``run.log``,
``run.progress``, ``run.metric`` and ``run.artifact``; optional fields are omitted, never null.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from pydantic_core import to_jsonable_python

from crewquarters._transport import BrokerClient
from crewquarters.errors import PlatformError
from crewquarters.redact import redact_text, redact_value

LEVELS = frozenset({"debug", "info", "warning", "error"})
_SHA256_HEX = frozenset("0123456789abcdef")


def _jsonable(value: Any) -> Any:
    """Make any value JSON-safe (datetimes become ISO strings, unknown objects their str())."""
    return to_jsonable_python(value, fallback=str)


def _default_echo(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


class EventsClient:
    def __init__(
        self,
        transport: BrokerClient,
        *,
        flush_interval: float = 1.0,
        max_batch: int = 50,
        max_buffer: int = 1000,
        echo: Callable[[str], None] | None = _default_echo,
    ) -> None:
        self._transport = transport
        self._flush_interval = flush_interval
        self._max_batch = max_batch
        self._max_buffer = max_buffer
        self._echo = echo
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    async def log(self, level: str, message: str, **fields: Any) -> None:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {sorted(LEVELS)}")
        payload = {
            "level": level,
            "message": redact_text(message)[:4000],
            "fields": redact_value(_jsonable(fields)),
        }
        if self._echo is not None:
            self._echo(json.dumps({"ts": _now(), **payload}, default=str))
        await self._add("run.log", payload)

    async def progress(self, percent: float | None, message: str, step: str | None = None) -> None:
        if percent is not None and not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        payload: dict[str, Any] = {"percent": percent, "message": redact_text(message)[:500]}
        if step is not None:
            payload["step"] = redact_text(step)[:120]
        await self._add("run.progress", payload)

    async def metric(self, name: str, value: float, unit: str | None = None) -> None:
        finite = isinstance(value, (int, float)) and math.isfinite(value)
        if isinstance(value, bool) or not finite:
            raise ValueError("metric value must be a finite number")
        _check_length("metric name", name, 120)
        payload: dict[str, Any] = {"name": name, "value": value}
        if unit is not None:
            _check_length("metric unit", unit, 32)
            payload["unit"] = unit
        await self._add("run.metric", payload)

    async def artifact(
        self,
        name: str,
        media_type: str | None = None,
        summary: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
    ) -> None:
        _check_length("artifact name", name, 200)
        payload: dict[str, Any] = {"name": name}
        if media_type is not None:
            _check_length("media type", media_type, 100)
            payload["mediaType"] = media_type
        if summary is not None:
            payload["summary"] = redact_text(summary)[:500]
        if size_bytes is not None:
            if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
                raise ValueError("size_bytes must be a non-negative integer")
            payload["bytes"] = size_bytes
        if sha256 is not None:
            if len(sha256) != 64 or not set(sha256) <= _SHA256_HEX:
                raise ValueError("sha256 must be 64 lowercase hex characters")
            payload["sha256"] = sha256
        await self._add("run.artifact", payload)

    async def _add(self, event_type: str, payload: dict[str, Any]) -> None:
        self._buffer.append(
            {
                "clientEventId": uuid.uuid4().hex,
                "type": event_type,
                "occurredAt": _now(),
                "payload": payload,
            }
        )
        overflow = len(self._buffer) - self._max_buffer
        if overflow > 0:
            del self._buffer[:overflow]
            self._dropped += overflow
        if len(self._buffer) >= self._max_batch:
            try:
                await self.flush()
            except Exception as exc:
                self._note(f"event delivery deferred: {exc}")

    async def _post(self, batch: list[dict[str, Any]]) -> None:
        await self._transport.request(
            "POST", "/events", operation="events", idempotent=True, json={"events": batch}
        )

    async def _salvage(self, batch: list[dict[str, Any]], error: PlatformError) -> None:
        """The broker rejected a batch: resend event by event and drop only the rejected ones."""
        dropped = 0
        if len(batch) == 1:
            dropped = 1
        else:
            for event in batch:
                try:
                    await self._post([event])
                except PlatformError as exc:
                    if exc.retryable:
                        raise
                    dropped += 1
        plural = "s" if dropped != 1 else ""
        self._note(f"dropped {dropped} event{plural} the broker rejected: {error.code}")

    async def flush(self) -> None:
        """Deliver buffered events.

        Rejected events are dropped; retryable failures keep them for a later flush."""
        async with self._lock:
            while self._buffer:
                batch = self._buffer[: self._max_batch]
                try:
                    await self._post(batch)
                except PlatformError as exc:
                    if exc.retryable:
                        raise
                    await self._salvage(batch, exc)
                del self._buffer[: len(batch)]

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._flush_periodically())

    async def _flush_periodically(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush()
            except Exception as exc:
                self._note(f"event delivery deferred: {exc}")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self.flush()
        except Exception as exc:
            self._note(f"could not deliver {len(self._buffer)} events: {exc}")
        if self._dropped:
            self._note(f"dropped {self._dropped} events because the buffer was full")

    def _note(self, message: str) -> None:
        if self._echo is not None:
            line = {"ts": _now(), "level": "warning", "message": f"crewquarters: {message}"}
            self._echo(json.dumps(line))


def _check_length(what: str, value: str, limit: int) -> None:
    if not value or len(value) > limit:
        raise ValueError(f"{what} must be 1-{limit} characters")


def _now() -> str:
    return datetime.now(UTC).isoformat()
