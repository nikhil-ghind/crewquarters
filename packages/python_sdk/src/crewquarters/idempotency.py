"""Run-scoped idempotency records that guard external side effects across retried attempts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, TypeVar, overload
from urllib.parse import quote

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from crewquarters._models import WireModel
from crewquarters._transport import BrokerClient
from crewquarters.errors import OutcomeUnknown, PlatformError

M = TypeVar("M", bound=BaseModel)
T = TypeVar("T")


class IdempotencyRecord(WireModel):
    key: str
    state: Literal["claimed", "in_progress", "completed"]
    result: Any = None
    claimed_by_attempt: int
    completed_at: datetime | None = None


class IdempotencyClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def claim(self, key: str, *, takeover: bool = False) -> IdempotencyRecord:
        data = await self._transport.request(
            "POST",
            "/idempotency/claim",
            operation="idempotency.claim",
            idempotent=True,
            json={"key": key, "takeover": takeover},
        )
        return IdempotencyRecord.model_validate(data)

    async def complete(self, key: str, result: Any) -> IdempotencyRecord:
        data = await self._transport.request(
            "POST",
            "/idempotency/complete",
            operation="idempotency.complete",
            idempotent=True,
            json={"key": key, "result": result},
        )
        return IdempotencyRecord.model_validate(data)

    async def get(self, key: str) -> IdempotencyRecord | None:
        try:
            data = await self._transport.request(
                "GET", f"/idempotency/{quote(key, safe='')}", operation="idempotency.get", idempotent=True
            )
        except PlatformError as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise
        return IdempotencyRecord.model_validate(data)

    @overload
    async def once(
        self,
        key: str,
        fn: Callable[[], Awaitable[M]],
        *,
        result_type: type[M],
        resume_in_progress: bool = False,
    ) -> M: ...

    @overload
    async def once(
        self,
        key: str,
        fn: Callable[[], Awaitable[Any]],
        *,
        result_type: None = None,
        resume_in_progress: bool = False,
    ) -> Any: ...

    async def once(
        self,
        key: str,
        fn: Callable[[], Awaitable[Any]],
        *,
        result_type: type[BaseModel] | None = None,
        resume_in_progress: bool = False,
    ) -> Any:
        """Run ``fn`` at most once per key for this run, across attempts.

        A completed key returns its stored result without calling ``fn``. A key claimed by an earlier
        attempt that never completed raises ``OutcomeUnknown`` unless ``resume_in_progress`` is set,
        which is only safe when ``fn`` is itself idempotent at the provider.
        """
        record = await self.claim(key)
        if record.state == "in_progress":
            if not resume_in_progress:
                raise OutcomeUnknown(
                    f"action {key} was started by attempt {record.claimed_by_attempt} and never completed",
                    details={"key": key},
                )
            record = await self.claim(key, takeover=True)
        if record.state == "completed":
            return _decode(record.result, result_type)
        value = await fn()
        await self.complete(key, _encode(value))
        return value


def _encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return to_jsonable_python(value)


def _decode(stored: Any, result_type: type[BaseModel] | None) -> Any:
    if result_type is None:
        return stored
    return result_type.model_validate(stored)
