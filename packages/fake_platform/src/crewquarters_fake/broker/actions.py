"""Broker action routes (``ctx.idempotency``), mirroring the control plane's action keys.

Only the call that creates a key is ``claimed``. Any later claim of an uncompleted key, from this
attempt or another, is ``in_doubt``: the side effect may already have happened. A completed key
returns its stored result.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import ActionRecord, utcnow
from crewquarters_fake.views import action_view

router = APIRouter()
KEY = Path(min_length=1, max_length=200)


class CompleteIn(BaseModel):
    result: Any = None


@router.post("/actions/{key}/claim")
async def claim(key: str = KEY, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.actions.claim")
        actions = auth.store.actions
        record = actions.get((auth.run.id, key))
        if record is None:
            record = ActionRecord(key, "claimed", auth.attempt)
            actions[(auth.run.id, key)] = record
            return action_view(record)
        if record.status == "completed":
            return action_view(record)
        record.status = "in_doubt"
        record.attempt = auth.attempt
        return action_view(record)

    return await auth.store.faults.run("broker.actions.claim", operation)


@router.post("/actions/{key}/complete")
async def complete(
    body: CompleteIn, key: str = KEY, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.actions.complete")
        record = auth.store.actions.get((auth.run.id, key))
        if record is None:
            raise ApiError(409, "ACTION_NOT_CLAIMED", "Claim the action key before completing it.")
        if record.status != "completed":
            record.status = "completed"
            record.result = body.result
            record.completed_at = utcnow()
        return action_view(record)

    return await auth.store.faults.run("broker.actions.complete", operation)
