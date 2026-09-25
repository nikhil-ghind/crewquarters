"""Idempotent ``/llm/chat`` for keyed requests (``idempotencyKey``).

The SDK retries keyed LLM calls after transport errors and 502/503/504, so without this a
retry could repeat a (billed) cloud call. For non-streaming requests the gateway keeps
``(holderType, holderId, idempotencyKey)`` -> the completed outcome for a bounded time:

* a duplicate that arrives while the first is running waits for it and gets its result;
* a duplicate that arrives later gets the stored response (or the stored final error:
  a refusal or invalid structured output, which the provider has already billed);
* errors that happen before or instead of a provider result (authorization, budgets,
  load failures, provider unreachable) are not stored, so a retry runs again;
* the same key with a different request body is ``422 INVALID_REQUEST``.

Streaming requests are never replayed; a keyed stream only claims its key while it runs,
and any duplicate during that time gets ``409 REQUEST_IN_PROGRESS``.

Limitation: the store is in-process memory. It is lost on a gateway restart and is not
shared between gateway processes (the appliance runs one). A durable store needs a table
and a migration owned by the control API.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from crewquarters_shared.errors import PlatformError, invalid

Key = tuple[str, str, str]
MAX_KEY_LENGTH = 200
# Body fields that do not change what is generated.
_IGNORED_FIELDS = frozenset({"idempotencyKey", "stream", "holder"})


class FinalError(PlatformError):
    """An error raised after the provider produced (and billed) a result. Stored and
    replayed for duplicates, unlike errors that happened before any result."""


def request_key(holder_type: str, holder_id: str, body: dict[str, Any]) -> Key | None:
    raw = body.get("idempotencyKey")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or len(raw) > MAX_KEY_LENGTH:
        raise invalid(
            "INVALID_REQUEST", f"idempotencyKey must be a string of at most {MAX_KEY_LENGTH}."
        )
    return (holder_type, holder_id, raw)


def fingerprint(body: dict[str, Any]) -> str:
    material = {k: v for k, v in body.items() if k not in _IGNORED_FIELDS}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass
class _Entry:
    fingerprint: str
    streaming: bool
    future: asyncio.Future[dict[str, Any]]
    expires: float | None = None  # set once completed
    # A stream whose generator never ran its cleanup must not hold its key forever.
    stale_after: float | None = None
    waiters: int = field(default=0)


def _copy(exc: PlatformError) -> PlatformError:
    return PlatformError(exc.code, exc.message, exc.status_code, dict(exc.details))


class IdempotencyStore:
    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: OrderedDict[Key, _Entry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def _evict(self) -> None:
        now = self._clock()
        for key, entry in list(self._entries.items()):
            if (entry.expires is not None and entry.expires <= now) or (
                entry.stale_after is not None and entry.stale_after <= now
            ):
                del self._entries[key]

    def _add(self, key: Key, entry: _Entry) -> None:
        """Make room (oldest completed entries first; in-flight ones always stay), then add."""
        completed = [k for k, e in self._entries.items() if e.expires is not None]
        while len(self._entries) >= self.max_entries and completed:
            del self._entries[completed.pop(0)]
        self._entries[key] = entry

    def _existing(self, key: Key, print_: str) -> _Entry | None:
        self._evict()
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.fingerprint != print_:
            raise invalid(
                "INVALID_REQUEST",
                "This idempotencyKey was already used for a different request.",
            )
        if entry.streaming:
            raise PlatformError(
                "REQUEST_IN_PROGRESS", "A request with this idempotencyKey is running.", 409
            )
        return entry

    async def run(
        self,
        key: Key,
        body: dict[str, Any],
        call: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Run ``call`` once per key; duplicates wait for or replay its outcome."""
        print_ = fingerprint(body)
        entry = self._existing(key, print_)
        if entry is not None:
            entry.waiters += 1
            try:
                result = await asyncio.shield(entry.future)
            except PlatformError as exc:
                raise _copy(exc) from None
            finally:
                entry.waiters -= 1
            return dict(result)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        # Retrieve the exception even when nobody waits, so asyncio does not log it.
        future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        entry = _Entry(print_, streaming=False, future=future)
        self._add(key, entry)
        try:
            result = await call()
        except FinalError as exc:
            entry.expires = self._clock() + self.ttl
            future.set_exception(_copy(exc))
            raise
        except PlatformError as exc:
            self._forget(key, entry)
            future.set_exception(_copy(exc))
            raise
        except BaseException:
            # Cancelled (client went away) or a bug: the outcome is unknown. Waiters get a
            # retryable error and the key is released for the next attempt.
            self._forget(key, entry)
            future.set_exception(
                PlatformError(
                    "MODEL_UNAVAILABLE",
                    "The original request with this idempotencyKey did not complete.",
                    503,
                )
            )
            raise
        entry.expires = self._clock() + self.ttl
        future.set_result(result)
        return dict(result)

    def _forget(self, key: Key, entry: _Entry) -> None:
        if self._entries.get(key) is entry:
            del self._entries[key]

    @contextlib.contextmanager
    def claim_stream(self, key: Key, body: dict[str, Any]) -> Iterator[None]:
        """Hold ``key`` for a stream's lifetime; any duplicate meanwhile gets 409."""
        print_ = fingerprint(body)
        self._evict()
        existing = self._entries.get(key)
        if existing is not None and existing.expires is None:
            raise PlatformError(
                "REQUEST_IN_PROGRESS", "A request with this idempotencyKey is running.", 409
            )
        if existing is not None:
            # A completed non-streaming result stays stored for its retries; streams are
            # never replayed, so this stream simply runs.
            yield
            return
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        entry = _Entry(print_, streaming=True, future=future)
        entry.stale_after = self._clock() + self.ttl
        self._add(key, entry)
        try:
            yield
        finally:
            self._forget(key, entry)
            if not future.done():
                future.cancel()
