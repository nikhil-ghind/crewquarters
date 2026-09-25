"""ORM models for tables owned by Akshay Sunil Navani (Person 2): model catalog,
installations, instances, leases, chat, and LLM usage (PLAN.md section 6.1).

Nikhil Hiro Ghind (Person 1) reviews and merges the migration so Alembic keeps one
linear history. Other services reach these tables through the model gateway's API.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from crewquarters_shared.db.base import Base
from crewquarters_shared.ids import uuid7

JSON = JSONB(none_as_null=True)

INSTANCE_STATES = (
    "NOT_LOADED",
    "LOADING",
    "READY",
    "DRAINING",
    "LOAD_ERROR",
    "RUNTIME_ERROR",
)
DOWNLOAD_STATES = ("NOT_INSTALLED", "DOWNLOADING", "INSTALLED", "DOWNLOAD_ERROR", "DELETING")


class ModelCatalogEntry(Base):
    __tablename__ = "model_catalog"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    backend: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expected_memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    context_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    trust_state: Mapped[str] = mapped_column(Text, nullable=False, default="catalog")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ModelInstallation(Base):
    __tablename__ = "model_installations"

    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_catalog.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, default="NOT_INSTALLED")
    revision: Mapped[str | None] = mapped_column(Text)
    disk_path: Mapped[str | None] = mapped_column(Text)
    bytes_done: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    current_file: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    license_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (CheckConstraint(f"state IN {DOWNLOAD_STATES!r}", name="state"),)


class ModelInstance(Base):
    __tablename__ = "model_instances"

    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_catalog.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, default="NOT_LOADED")
    stage: Mapped[str | None] = mapped_column(Text)
    drain_reason: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    observed: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    load_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"state IN {INSTANCE_STATES!r}", name="state"),
        CheckConstraint(
            "drain_reason IS NULL OR drain_reason IN ('idle', 'manual', 'stopping')", name="drain"
        ),
    )


class ModelLease(Base):
    __tablename__ = "model_leases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_catalog.id", ondelete="CASCADE"), nullable=False
    )
    holder_type: Mapped[str] = mapped_column(Text, nullable=False)
    holder_id: Mapped[str] = mapped_column(Text, nullable=False)
    holder_label: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("holder_type IN ('run', 'chat', 'manual')", name="holder_type"),
        Index(
            "ix_model_leases_active",
            "model_id",
            postgresql_where=text("released_at IS NULL"),
        ),
        Index(
            "uq_model_leases_active_holder",
            "model_id",
            "holder_type",
            "holder_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    model_profile: Mapped[str] = mapped_column(ForeignKey("model_catalog.id"), nullable=False)
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    retrieval_mode: Mapped[str] = mapped_column(Text, nullable=False, default="when_relevant")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active_lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("retrieval_mode IN ('when_relevant', 'only_knowledge')", name="mode"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="complete")
    citations: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    model: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint("status IN ('streaming', 'complete', 'stopped', 'failed')", name="status"),
    )


class LlmUsage(Base):
    """One row per inference request; aggregated for budgets and metrics. No prompts."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    holder_type: Mapped[str] = mapped_column(Text, nullable=False)
    holder_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(Text, nullable=False, default="ok")
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_llm_usage_day_provider", "day", "provider"),
        Index("ix_llm_usage_holder", "holder_type", "holder_id"),
    )
