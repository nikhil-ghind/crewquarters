"""ORM models for the control-plane tables owned by Person 1 (PLAN.md section 6.1).

Tables owned by other people (models, knowledge, secrets, connections, chat) are
added by their owners through reviewed migrations; they are not declared here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from crewquarters_shared.db.base import Base
from crewquarters_shared.ids import uuid7

JSON = JSONB(none_as_null=True)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --- Identity -----------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    username: Mapped[str] = mapped_column(Text, nullable=False)
    username_normalized: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="owner")
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (CheckConstraint("role IN ('owner', 'member')", name="role"),)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _pk()
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    value_type: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = _updated()


# --- Catalog and installations -------------------------------------------------


class AgentCatalogEntry(Base):
    __tablename__ = "agent_catalog_entries"

    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    publisher: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    current_version: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    trust_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (
        CheckConstraint("source IN ('bundled', 'imported')", name="source"),
        CheckConstraint("trust_status IN ('curated', 'imported_unreviewed')", name="trust_status"),
    )


class AgentVersion(Base):
    """Immutable manifest snapshot; a database trigger rejects UPDATE."""

    __tablename__ = "agent_versions"

    id: Mapped[uuid.UUID] = _pk()
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent_catalog_entries.agent_id"), nullable=False
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    image_ref: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(Text, nullable=False)
    sdk_protocol: Mapped[str] = mapped_column(Text, nullable=False)
    architectures: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("agent_id", "version"),)


class AgentInstallation(Base):
    __tablename__ = "agent_installations"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent_catalog_entries.agent_id"), nullable=False
    )
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_versions.id"), nullable=False
    )
    approved_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_versions.id"))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    approved_permissions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_bindings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    needs_reapproval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Runs -----------------------------------------------------------------------

RUN_STATES = (
    "QUEUED",
    "PREPARING",
    "LOADING_MODEL",
    "RUNNING",
    "WAITING_INPUT",
    "CANCELLING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "INTERRUPTED",
)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _pk()
    installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_installations.id"), nullable=False, index=True
    )
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_versions.id"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("schedules.id"))
    # A run another agent's run started (trigger "agent"): the starter, the caller's key that
    # makes the start idempotent, and the bounded input the caller passed (untrusted).
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    start_key: Mapped[str | None] = mapped_column(Text)
    trigger_input: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(Text, nullable=False, default="QUEUED")
    state_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    permissions_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_bindings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    active_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_wait_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    active_seconds_used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_wait_seconds_used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (
        CheckConstraint("trigger IN ('manual', 'schedule', 'agent')", name="trigger"),
        CheckConstraint(f"state IN {RUN_STATES!r}", name="state"),
        Index(
            "uq_agent_runs_schedule_occurrence",
            "schedule_id",
            "scheduled_for",
            unique=True,
            postgresql_where=text("schedule_id IS NOT NULL"),
        ),
        Index("ix_agent_runs_state", "state"),
        Index(
            "uq_agent_runs_agent_start",
            "parent_run_id",
            "start_key",
            unique=True,
            postgresql_where=text("parent_run_id IS NOT NULL"),
        ),
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="starting")
    runtime_ref: Mapped[str | None] = mapped_column(Text)
    capability_token_id: Mapped[str | None] = mapped_column(Text)
    heartbeat_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("run_id", "attempt"),
        CheckConstraint(
            "state IN ('starting', 'running', 'exited', 'interrupted', 'cancelled')", name="state"
        ),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    # Agent events only: the SDK's id (retry dedupe) and when the agent emitted the event.
    client_event_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        Index(
            "uq_run_events_client_event",
            "run_id",
            "client_event_id",
            unique=True,
            postgresql_where=text("client_event_id IS NOT NULL"),
        ),
    )


class InputRequest(Base):
    __tablename__ = "input_requests"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    answer: Mapped[Any] = mapped_column(JSON, nullable=True)
    answered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("run_id", "key"),
        CheckConstraint("state IN ('pending', 'answered', 'cancelled', 'expired')", name="state"),
        Index("ix_input_requests_pending", "state", postgresql_where=text("state = 'pending'")),
    )


class IdempotencyAction(Base):
    """Agent-side action keys backing ``ctx.idempotency`` (claim/complete/retrieve)."""

    __tablename__ = "idempotency_actions"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    # Random per SDK claim() call and reused on its retries: identifies the claimant.
    claim_token: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("run_id", "key"),
        CheckConstraint("state IN ('claimed', 'in_doubt', 'completed')", name="state"),
    )


# --- Scheduling and jobs --------------------------------------------------------


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = _pk()
    installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_installations.id"), nullable=False, index=True
    )
    cron: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    misfire_policy: Mapped[str] = mapped_column(Text, nullable=False, default="fire_once")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("misfire_policy IN ('fire_once', 'skip')", name="misfire_policy"),
        Index(
            "ix_schedules_due",
            "next_run_at",
            postgresql_where=text("enabled AND deleted_at IS NULL"),
        ),
    )


JOB_STATES = ("available", "claimed", "succeeded", "dead", "cancelled")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="available")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"state IN {JOB_STATES!r}", name="state"),
        Index(
            "ix_jobs_available",
            "available_at",
            "id",
            postgresql_where=text("state = 'available'"),
        ),
        Index("ix_jobs_claimed_lease", "lease_until", postgresql_where=text("state = 'claimed'")),
        Index(
            "uq_jobs_live_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND state IN ('available', 'claimed')"),
        ),
    )


# --- API idempotency and audit --------------------------------------------------


class IdempotencyRecord(Base):
    """Replay store for mutating HTTP requests that carry an ``Idempotency-Key``."""

    __tablename__ = "idempotency_records"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("user_id", "key"),)


class AuditEvent(Base):
    """Append-only; a database trigger rejects UPDATE and DELETE."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _pk()
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False, default="success")
    request_id: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'system', 'service', 'anonymous')", name="actor"),
        CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="outcome"),
    )
