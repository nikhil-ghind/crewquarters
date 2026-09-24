"""Structured run events (log, progress, metric, artifact), buffered and delivered in batches."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from crewquarters._transport import BrokerClient
from crewquarters.errors import PlatformError
from crewquarters.redact import redact_text, redact_value

LEVELS = frozenset({"debug", "info", "warning", "error"})


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
        payload = {"level": level, "message": redact_text(message)[:4000], "fields": redact_value(fields)}
        if self._echo is not None:
            self._echo(json.dumps({"ts": _now(), **payload}, default=str))
        await self._add("log", payload)

    async def progress(self, percent: float | None, message: str, step: str | None = None) -> None:
        if percent is not None and not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        await self._add("progress", {"percent": percent, "message": redact_text(message)[:500], "step": step})

    async def metric(self, name: str, value: float, unit: str | None = None) -> None:
        await self._add("metric", {"name": name, "value": value, "unit": unit})

    async def artifact(
        self, name: str, media_type: str, summary: str | None = None, size_bytes: int | None = None
    ) -> None:
        payload = {"name": name, "mediaType": media_type, "summary": summary, "sizeBytes": size_bytes}
        await self._add("artifact", payload)

    async def _add(self, event_type: str, payload: dict[str, Any]) -> None:
        self._buffer.append(
            {"clientEventId": uuid.uuid4().hex, "type": event_type, "occurredAt": _now(), "payload": payload}
        )
        overflow = len(self._buffer) - self._max_buffer
        if overflow > 0:
            del self._buffer[:overflow]
            self._dropped += overflow
        if len(self._buffer) >= self._max_batch:
            try:
                await self.flush()
            except PlatformError as exc:
                self._note(f"event delivery deferred: {exc}")

    async def flush(self) -> None:
        async with self._lock:
            while self._buffer:
                batch = self._buffer[: self._max_batch]
                await self._transport.request(
                    "POST", "/events", operation="events", idempotent=True, json={"events": batch}
                )
                del self._buffer[: len(batch)]

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._flush_periodically())

    async def _flush_periodically(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush()
            except PlatformError as exc:
                self._note(f"event delivery deferred: {exc}")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self.flush()
        except PlatformError as exc:
            self._note(f"could not deliver {len(self._buffer)} events: {exc}")
        if self._dropped:
            self._note(f"dropped {self._dropped} events because the buffer was full")

    def _note(self, message: str) -> None:
        if self._echo is not None:
            self._echo(json.dumps({"ts": _now(), "level": "warning", "message": f"crewquarters: {message}"}))


def _now() -> str:
    return datetime.now(UTC).isoformat()
