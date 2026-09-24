"""Broker idempotency-record routes (spec section 4.3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import IdempotencyRecord, utcnow
from crewquarters_fake.views import idempotency_view

router = APIRouter()


class ClaimIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    takeover: bool = False


class CompleteIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    result: Any = None


@router.post("/idempotency/claim")
async def claim(body: ClaimIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.idempotency.claim")
        records = auth.store.idempotency
        record = records.get((auth.run.id, body.key))
        if record is None:
            record = IdempotencyRecord(body.key, "in_progress", None, auth.attempt)
            records[(auth.run.id, body.key)] = record
            return idempotency_view(record, state="claimed")
        if record.state == "completed":
            return idempotency_view(record)
        if record.claimed_by_attempt == auth.attempt or body.takeover:
            record.claimed_by_attempt = auth.attempt
            return idempotency_view(record, state="claimed")
        return idempotency_view(record)

    return await auth.store.faults.run("broker.idempotency.claim", operation)


@router.post("/idempotency/complete")
async def complete(body: CompleteIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.idempotency.complete")
        record = auth.store.idempotency.get((auth.run.id, body.key))
        if record is None:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", f"key {body.key} was never claimed")
        if record.state == "completed":
            if record.result == body.result:
                return idempotency_view(record)
            raise ApiError(
                409, "IDEMPOTENCY_CONFLICT", f"key {body.key} already completed with a different result"
            )
        if record.claimed_by_attempt != auth.attempt:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                f"key {body.key} is claimed by attempt {record.claimed_by_attempt}",
            )
        record.state = "completed"
        record.result = body.result
        record.completed_at = utcnow()
        return idempotency_view(record)

    return await auth.store.faults.run("broker.idempotency.complete", operation)


@router.get("/idempotency/{key}")
async def get_record(key: str, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.idempotency.get")
        record = auth.store.idempotency.get((auth.run.id, key))
        if record is None:
            raise ApiError(404, "NOT_FOUND", f"no idempotency record for {key}")
        return idempotency_view(record)

    return await auth.store.faults.run("broker.idempotency.get", operation)
