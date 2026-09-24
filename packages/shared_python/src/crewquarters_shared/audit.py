"""Append-only audit records (PLAN.md section 15.1). Metadata is always redacted."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared.db.models import AuditEvent
from crewquarters_shared.redaction import redact


def record(
    session: AsyncSession,
    *,
    action: str,
    actor_type: str,
    actor_id: object | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    outcome: str = "success",
    request_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_type=actor_type,
        actor_id=str(actor_id) if actor_id is not None else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        request_id=request_id,
        ip_address=ip_address,
        metadata_=redact(metadata or {}),
    )
    session.add(event)
    return event
