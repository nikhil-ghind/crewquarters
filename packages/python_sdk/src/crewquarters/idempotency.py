"""Run-scoped action keys that guard external side effects across retried attempts.

The broker passes these through to the control plane's action records: the call that creates a
key gets ``claimed``; any later claim of a key that was never completed gets ``in_doubt`` (the side
effect may already have happened); a completed key returns its stored result.

A claim is retried after a lost response. Each ``claim()`` call sends one random
``X-Claim-Token`` on all of its retries, so the platform recognises a retry of the call that
created the key and answers ``claimed`` again instead of ``in_doubt``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar, overload
from urllib.parse import quote

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from crewquarters._models import WireModel
from crewquarters._transport import BrokerClient
from crewquarters.errors import OutcomeUnknown

CLAIM_TOKEN_HEADER = "X-Claim-Token"  # noqa: S105 - a header name
M = TypeVar("M", bound=BaseModel)
T = TypeVar("T")


class ActionRecord(WireModel):
    key: str
    status: Literal["claimed", "in_doubt", "completed"]
    result: Any = None


class IdempotencyClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def claim(self, key: str) -> ActionRecord:
        # Safe to retry only because every retry carries the same claim token.
        data = await self._transport.request(
            "POST",
            f"/actions/{quote(key, safe='')}/claim",
            operation="actions.claim",
            idempotent=True,
            headers={CLAIM_TOKEN_HEADER: uuid.uuid4().hex},
        )
        return ActionRecord.model_validate(data)

    async def complete(self, key: str, result: Any) -> ActionRecord:
        data = await self._transport.request(
            "POST",
            f"/actions/{quote(key, safe='')}/complete",
            operation="actions.complete",
            idempotent=True,
            json={"result": result},
        )
        return ActionRecord.model_validate(data)

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

        A completed key returns its stored result without calling ``fn``. A key that was claimed
        and never completed is ``in_doubt`` and raises ``OutcomeUnknown`` unless
        ``resume_in_progress`` is set, which is only safe when ``fn`` is itself idempotent at the
        provider (for example, it passes a provider idempotency key).
        """
        record = await self.claim(key)
        if record.status == "completed":
            return _decode(record.result, result_type)
        if record.status == "in_doubt" and not resume_in_progress:
            raise OutcomeUnknown(
                f"action {key} was started earlier and never completed; it may have happened",
                details={"key": key},
            )
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
