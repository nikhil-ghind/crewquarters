"""HTTP idempotency for mutating requests (PLAN.md section 4.3).

Clients may send ``Idempotency-Key``. The first request stores its response in
the same transaction as its effect; a retry with the same key and body replays that
response, and a retry with a different body is rejected. Requests without a key get
a generated one, echoed in the response header.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared.db.models import IdempotencyRecord
from crewquarters_shared.errors import conflict, invalid

HEADER = "Idempotency-Key"


@dataclass
class Idempotency:
    key: str
    record_id: uuid.UUID | None
    replay: Response | None


async def begin(db: AsyncSession, request: Request, user_id: uuid.UUID, body: Any) -> Idempotency:
    key = request.headers.get(HEADER)
    if key is None:
        return Idempotency(key=str(uuid.uuid4()), record_id=None, replay=None)
    if not 8 <= len(key) <= 200:
        raise invalid("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 8-200 characters.")
    digest = hashlib.sha256(
        json.dumps(
            {"m": request.method, "p": request.url.path, "b": jsonable_encoder(body)},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    record_id = await db.scalar(
        pg_insert(IdempotencyRecord)
        .values(
            user_id=user_id,
            key=key,
            method=request.method,
            path=request.url.path,
            request_hash=digest,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "key"])
        .returning(IdempotencyRecord.id)
    )
    if record_id is not None:
        return Idempotency(key=key, record_id=record_id, replay=None)
    existing = await db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key
        )
    )
    assert existing is not None
    if existing.request_hash != digest:
        raise conflict(
            "IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used for a different request."
        )
    if existing.status_code is None:
        raise conflict("REQUEST_IN_PROGRESS", "The original request is still being processed.")
    headers = {HEADER: key, "Idempotent-Replayed": "true"}
    replay: Response = (
        Response(status_code=204, headers=headers)
        if existing.status_code == 204
        else JSONResponse(existing.response_body, status_code=existing.status_code, headers=headers)
    )
    return Idempotency(key=key, record_id=existing.id, replay=replay)


async def finish(db: AsyncSession, idem: Idempotency, status_code: int, body: Any) -> Response:
    """Store the response (same transaction as the effect), commit, and return it.
    A ``204`` stores and returns no body."""
    encoded = None if status_code == 204 else jsonable_encoder(body, by_alias=True)
    if idem.record_id is not None:
        record = await db.get(IdempotencyRecord, idem.record_id)
        assert record is not None
        record.status_code = status_code
        record.response_body = encoded
    await db.commit()
    if status_code == 204:
        return Response(status_code=204, headers={HEADER: idem.key})
    return JSONResponse(encoded, status_code=status_code, headers={HEADER: idem.key})
