"""Broker-owned tables (PLAN.md section 6.1): ``oauth_connections`` and ``telephony_calls``.

Secrets live in ``encrypted_secrets`` (see ``crewquarters_secret_store.db``). Full phone
numbers are never stored: only a keyed hash and the last four digits.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from crewquarters_shared.db.base import Base
from crewquarters_shared.ids import uuid7


class OAuthConnection(Base):
    __tablename__ = "oauth_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # The Gmail address when gmail.readonly is granted; we request no identity scopes.
    provider_subject: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    encrypted_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("encrypted_secrets.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="CONNECTED")
    status_detail: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("provider IN ('google')", name="provider"),
        CheckConstraint("status IN ('CONNECTED', 'NEEDS_ATTENTION')", name="status"),
        UniqueConstraint("user_id", "provider", "provider_subject"),
    )


class TelephonyCall(Base):
    __tablename__ = "telephony_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    provider_sid: Mapped[str | None] = mapped_column(Text, unique=True)
    destination_hash: Mapped[str] = mapped_column(Text, nullable=False)
    destination_last4: Mapped[str] = mapped_column(Text, nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    response_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # CREATING until Twilio accepts the call; IN_DOUBT if that request's outcome is unknown.
    state: Mapped[str] = mapped_column(Text, nullable=False, default="CREATING")
    transcript: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("run_id", "idempotency_key"),)
