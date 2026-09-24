"""Run lifecycle operations shared by the control API, the worker, and the reconciler.

Every function takes an ``AsyncSession`` and leaves committing to the caller, so a
state change, its run event, and any job it enqueues commit atomically.
Run rows are locked ``FOR UPDATE`` before any change, which also serializes the
per-run event sequence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared import jobs
from crewquarters_shared.db.models import (
    AgentInstallation,
    AgentRun,
    AgentVersion,
    IdempotencyAction,
    InputRequest,
    RunAttempt,
    RunEvent,
)
from crewquarters_shared.errors import PlatformError, conflict, invalid, not_found
from crewquarters_shared.ids import uuid7
from crewquarters_shared.redaction import redact
from crewquarters_shared.runs.states import (
    ACTIVE_CLOCK_STATES,
    CANCELLABLE_STATES,
    TERMINAL_STATES,
    WAIT_CLOCK_STATES,
    RunState,
    can_transition,
)
from crewquarters_shared.schema_guard import check_schema, check_size
from crewquarters_shared.timeutil import utcnow

JOB_DISPATCH = "run.dispatch"
JOB_CANCEL = "run.cancel"
JOB_STOP = "run.stop"

MAX_RESULT_BYTES = 1_048_576
MAX_EVENT_BYTES = 16 * 1024
MAX_PREVIEW_BYTES = 64 * 1024
MAX_ANSWER_BYTES = 64 * 1024
AGENT_EVENT_TYPES = frozenset({"run.log", "run.progress", "run.metric", "run.artifact"})


def dispatch_key(run_id: uuid.UUID, attempt: int) -> str:
    return f"run:{run_id}:attempt:{attempt}"


def schedule_key(schedule_id: uuid.UUID, scheduled_for: datetime) -> str:
    return f"schedule:{schedule_id}:{scheduled_for.isoformat()}"


# --- Loading --------------------------------------------------------------------


async def lock_run(session: AsyncSession, run_id: uuid.UUID) -> AgentRun:
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise not_found("Run", run_id)
    return run


async def try_lock_run(session: AsyncSession, run_id: uuid.UUID) -> AgentRun | None:
    """Lock the run row without waiting; ``None`` if missing or locked by someone else."""
    run: AgentRun | None = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return run


async def get_attempt(session: AsyncSession, run_id: uuid.UUID, attempt: int) -> RunAttempt | None:
    result: RunAttempt | None = await session.scalar(
        select(RunAttempt).where(RunAttempt.run_id == run_id, RunAttempt.attempt == attempt)
    )
    return result


def require_attempt(run: AgentRun, attempt: int) -> None:
    """Reject calls from an attempt that is no longer the run's current attempt."""
    if attempt != run.current_attempt:
        raise conflict(
            "STALE_ATTEMPT",
            "This run attempt is no longer current.",
            attempt=attempt,
            currentAttempt=run.current_attempt,
        )


# --- Events ---------------------------------------------------------------------


async def append_event(
    session: AsyncSession, run: AgentRun, event_type: str, payload: dict[str, Any]
) -> RunEvent:
    """Append an event. The caller must hold the run row lock."""
    last = await session.scalar(
        select(func.coalesce(func.max(RunEvent.sequence), 0)).where(RunEvent.run_id == run.id)
    )
    event = RunEvent(
        run_id=run.id,
        sequence=int(last or 0) + 1,
        attempt=run.current_attempt,
        type=event_type,
        payload=payload,
        created_at=utcnow(),
    )
    session.add(event)
    await session.flush()
    return event


# --- State transitions ----------------------------------------------------------


def _account(run: AgentRun, now: datetime) -> None:
    elapsed = max(0.0, (now - run.state_entered_at).total_seconds())
    if run.state in ACTIVE_CLOCK_STATES:
        run.active_seconds_used += elapsed
    elif run.state in WAIT_CLOCK_STATES:
        run.input_wait_seconds_used += elapsed


def active_seconds(run: AgentRun, now: datetime) -> float:
    extra = (now - run.state_entered_at).total_seconds() if run.state in ACTIVE_CLOCK_STATES else 0
    return run.active_seconds_used + max(0.0, extra)


def wait_seconds(run: AgentRun, now: datetime) -> float:
    extra = (now - run.state_entered_at).total_seconds() if run.state in WAIT_CLOCK_STATES else 0
    return run.input_wait_seconds_used + max(0.0, extra)


async def transition(
    session: AsyncSession,
    run: AgentRun,
    target: RunState,
    *,
    reason: str | None = None,
    error: dict[str, Any] | None = None,
    retryable: bool | None = None,
) -> RunEvent:
    current = RunState(run.state)
    if not can_transition(current, target):
        raise conflict(
            "INVALID_RUN_STATE",
            f"Run cannot move from {current} to {target}.",
            state=str(current),
            target=str(target),
        )
    now = utcnow()
    _account(run, now)
    run.state = target
    run.state_entered_at = now
    if target == RunState.RUNNING and run.started_at is None:
        run.started_at = now
    if target in TERMINAL_STATES:
        run.finished_at = now
    elif target == RunState.QUEUED:
        run.finished_at = None
    if error is not None:
        run.error = error
    if retryable is not None:
        run.retryable = retryable
    payload: dict[str, Any] = {"from": str(current), "to": str(target)}
    if reason:
        payload["reason"] = reason
    if error:
        payload["errorCode"] = error.get("code")
    return await append_event(session, run, "run.state_changed", payload)


# --- Creation, cancel, retry ----------------------------------------------------


@dataclass(frozen=True)
class NewRun:
    run: AgentRun
    created: bool


async def create_run(
    session: AsyncSession,
    installation: AgentInstallation,
    version: AgentVersion,
    *,
    trigger: str,
    created_by: uuid.UUID | None = None,
    schedule_id: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
) -> NewRun:
    """Create a QUEUED run and its first dispatch job.

    Scheduled runs are inserted with ``ON CONFLICT DO NOTHING`` against the unique
    ``(schedule_id, scheduled_for)`` index, which is the final duplicate defense.
    """
    resources = version.manifest["spec"]["resources"]
    run_id = uuid7()
    values = {
        "id": run_id,
        "installation_id": installation.id,
        "agent_version_id": version.id,
        "trigger": trigger,
        "schedule_id": schedule_id,
        "scheduled_for": scheduled_for,
        "state": RunState.QUEUED.value,
        "state_entered_at": utcnow(),
        "current_attempt": 1,
        "config_snapshot": installation.config,
        "permissions_snapshot": installation.approved_permissions,
        "model_bindings": installation.model_bindings,
        "active_timeout_seconds": int(resources["activeTimeoutSeconds"]),
        "max_input_wait_seconds": int(resources.get("maxInputWaitSeconds", 86_400)),
        "active_seconds_used": 0.0,
        "input_wait_seconds_used": 0.0,
        "retryable": False,
        "created_by": created_by,
    }
    stmt = pg_insert(AgentRun).values(**values)
    if schedule_id is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["schedule_id", "scheduled_for"],
            index_where=AgentRun.schedule_id.isnot(None),
        )
    inserted = await session.scalar(stmt.returning(AgentRun.id))
    if inserted is None:
        existing = await session.scalar(
            select(AgentRun).where(
                AgentRun.schedule_id == schedule_id, AgentRun.scheduled_for == scheduled_for
            )
        )
        assert existing is not None
        return NewRun(existing, False)
    run = await lock_run(session, run_id)
    await append_event(session, run, "run.state_changed", {"from": None, "to": "QUEUED"})
    await jobs.enqueue(
        session,
        JOB_DISPATCH,
        {"runId": str(run.id), "attempt": 1},
        dedupe_key=dispatch_key(run.id, 1),
    )
    return NewRun(run, True)


async def request_cancel(session: AsyncSession, run: AgentRun, *, reason: str) -> AgentRun:
    if RunState(run.state) not in CANCELLABLE_STATES:
        if run.state in (RunState.CANCELLING, RunState.CANCELLED):
            return run
        raise conflict("RUN_NOT_CANCELLABLE", f"A {run.state} run cannot be cancelled.")
    run.cancel_requested_at = utcnow()
    if run.state == RunState.QUEUED:
        await jobs.cancel_by_dedupe(session, dispatch_key(run.id, run.current_attempt))
        await transition(session, run, RunState.CANCELLED, reason=reason)
    else:
        await transition(session, run, RunState.CANCELLING, reason=reason)
        await jobs.enqueue(
            session,
            JOB_CANCEL,
            {"runId": str(run.id), "attempt": run.current_attempt},
            dedupe_key=f"run:{run.id}:cancel:{run.current_attempt}",
        )
    await close_pending_inputs(session, run, "cancelled")
    return run


async def retry(session: AsyncSession, run: AgentRun) -> AgentRun:
    state = RunState(run.state)
    if state == RunState.FAILED and not run.retryable:
        raise conflict("RUN_NOT_RETRYABLE", "This failure is not retryable.")
    if state not in (RunState.FAILED, RunState.INTERRUPTED):
        raise conflict("RUN_NOT_RETRYABLE", f"A {run.state} run cannot be retried.")
    run.current_attempt += 1
    run.result = None
    run.error = None
    run.retryable = False
    run.cancel_requested_at = None
    run.acknowledged_at = None
    # Both time limits are per attempt.
    run.active_seconds_used = 0.0
    run.input_wait_seconds_used = 0.0
    await transition(session, run, RunState.QUEUED, reason="owner_retry")
    await jobs.enqueue(
        session,
        JOB_DISPATCH,
        {"runId": str(run.id), "attempt": run.current_attempt},
        dedupe_key=dispatch_key(run.id, run.current_attempt),
    )
    return run


# --- Attempt lifecycle (worker and SDK via broker) ------------------------------


async def begin_attempt(session: AsyncSession, run: AgentRun, heartbeat_seconds: int) -> RunAttempt:
    """QUEUED -> PREPARING and create the attempt row. Idempotent for crash recovery."""
    attempt = await get_attempt(session, run.id, run.current_attempt)
    if run.state == RunState.QUEUED:
        await transition(session, run, RunState.PREPARING)
    if attempt is None:
        attempt = RunAttempt(
            run_id=run.id,
            attempt=run.current_attempt,
            state="starting",
            heartbeat_expires_at=utcnow() + timedelta(seconds=heartbeat_seconds),
        )
        session.add(attempt)
        await session.flush()
    return attempt


async def handshake(
    session: AsyncSession, run_id: uuid.UUID, attempt_no: int, heartbeat_seconds: int
) -> AgentRun:
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    attempt = await get_attempt(session, run_id, attempt_no)
    if attempt is None:
        raise conflict("ATTEMPT_NOT_STARTED", "The attempt has not been started.")
    if run.state == RunState.PREPARING:
        await transition(session, run, RunState.RUNNING)
    elif run.state not in (RunState.RUNNING, RunState.CANCELLING):
        raise conflict("INVALID_RUN_STATE", f"Cannot hand shake while {run.state}.")
    attempt.state = "running"
    attempt.heartbeat_expires_at = utcnow() + timedelta(seconds=heartbeat_seconds)
    return run


async def heartbeat(
    session: AsyncSession, run_id: uuid.UUID, attempt_no: int, heartbeat_seconds: int
) -> dict[str, Any]:
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    attempt = await get_attempt(session, run_id, attempt_no)
    if attempt is None:
        raise conflict("ATTEMPT_NOT_STARTED", "The attempt has not been started.")
    if RunState(run.state) in TERMINAL_STATES:
        return {"state": run.state, "cancelRequested": True}
    attempt.heartbeat_expires_at = utcnow() + timedelta(seconds=heartbeat_seconds)
    return {"state": run.state, "cancelRequested": run.cancel_requested_at is not None}


async def set_model_loading(
    session: AsyncSession, run_id: uuid.UUID, attempt_no: int, loading: bool, model: str | None
) -> AgentRun:
    """Model gateway lease signal. Only moves RUNNING <-> LOADING_MODEL; in any other
    state (waiting for input, cancelling, preparing) it is a no-op."""
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    reason = f"model:{model}" if model else None
    if loading and run.state == RunState.RUNNING:
        await transition(session, run, RunState.LOADING_MODEL, reason=reason)
    elif not loading and run.state == RunState.LOADING_MODEL:
        await transition(session, run, RunState.RUNNING, reason=reason)
    return run


async def record_agent_event(
    session: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    event_type: str,
    payload: dict[str, Any],
) -> RunEvent:
    if event_type not in AGENT_EVENT_TYPES:
        raise invalid("INVALID_EVENT_TYPE", f"Agents cannot emit {event_type}.")
    check_size(payload, MAX_EVENT_BYTES, "EVENT_TOO_LARGE", "The event payload")
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    if RunState(run.state) in TERMINAL_STATES:
        raise conflict("RUN_FINISHED", "The run has already finished.")
    return await append_event(session, run, event_type, redact(payload))


async def finish(
    session: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    *,
    status: str,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
) -> AgentRun:
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    if len(json.dumps(result or {}, default=str)) > MAX_RESULT_BYTES:
        raise invalid("RESULT_TOO_LARGE", "Run results are limited to 1 MiB.")
    attempt = await get_attempt(session, run_id, attempt_no)
    if attempt is not None:
        attempt.state = "exited"
        attempt.ended_at = utcnow()
    if RunState(run.state) in TERMINAL_STATES:
        return run  # duplicate result post; first one wins
    run.result = result
    if run.state == RunState.CANCELLING:
        await transition(session, run, RunState.CANCELLED, reason="finished_during_cancel")
        return run
    if run.state in (RunState.LOADING_MODEL, RunState.WAITING_INPUT):
        await transition(session, run, RunState.RUNNING, reason="finishing")
    if status == "succeeded":
        await append_event(session, run, "run.result", {"status": "succeeded"})
        await transition(session, run, RunState.SUCCEEDED)
    else:
        err = error or {"code": "AGENT_FAILED", "message": "The agent reported a failure."}
        err = redact(err)
        await append_event(session, run, "run.result", {"status": "failed"})
        await transition(
            session, run, RunState.FAILED, error=err, retryable=bool(err.get("retryable", True))
        )
    await close_pending_inputs(session, run, "cancelled")
    return run


async def fail_run(
    session: AsyncSession,
    run: AgentRun,
    code: str,
    message: str,
    *,
    retryable: bool,
    interrupted: bool = False,
) -> None:
    """Platform-side failure (timeout, crash, dead job). Enqueues a container stop."""
    target = RunState.INTERRUPTED if interrupted else RunState.FAILED
    if RunState(run.state) in TERMINAL_STATES or not can_transition(run.state, target):
        return
    error = {"code": code, "message": message, "retryable": retryable}
    await append_event(session, run, "run.error", error)
    await transition(session, run, target, error=error, retryable=retryable)
    attempt = await get_attempt(session, run.id, run.current_attempt)
    if attempt is not None and attempt.ended_at is None:
        attempt.state = "interrupted" if interrupted else "exited"
        attempt.ended_at = utcnow()
        attempt.error = error
        if attempt.runtime_ref:
            await jobs.enqueue(
                session,
                JOB_STOP,
                {"runId": str(run.id), "attempt": run.current_attempt},
                dedupe_key=f"run:{run.id}:stop:{run.current_attempt}",
            )
    await close_pending_inputs(session, run, "cancelled")


# --- Human input ----------------------------------------------------------------


async def ask(
    session: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    *,
    key: str,
    title: str,
    prompt: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    preview: dict[str, Any] | None = None,
) -> InputRequest:
    """Create or return the input request for ``(run, key)``.

    A retried attempt that asks the same stable key receives the existing request:
    an answer given to an earlier attempt is returned as-is, and a request that an
    earlier attempt left cancelled or expired (restart, crash) is reopened.
    """
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    if not run.permissions_snapshot.get("userInput"):
        raise PlatformError(
            "PERMISSION_DENIED", "This agent was not approved to ask questions.", 403
        )
    if run.state not in (RunState.RUNNING, RunState.WAITING_INPUT):
        raise conflict("INVALID_RUN_STATE", f"Cannot ask for input while {run.state}.")
    existing = await session.scalar(
        select(InputRequest)
        .where(InputRequest.run_id == run_id, InputRequest.key == key)
        .with_for_update()
    )
    if existing is not None and not (
        existing.state in ("cancelled", "expired") and existing.attempt < attempt_no
    ):
        if existing.state == "pending" and run.state == RunState.RUNNING:
            await transition(session, run, RunState.WAITING_INPUT, reason=f"input:{key}")
        return existing
    check_schema(schema, code="INVALID_INPUT_SCHEMA", allow_patterns=False)
    if preview is not None:
        check_size(preview, MAX_PREVIEW_BYTES, "PREVIEW_TOO_LARGE", "The preview")
    now = utcnow()
    remaining = run.max_input_wait_seconds - wait_seconds(run, now)
    if timeout_seconds <= 0 or timeout_seconds > remaining:
        raise invalid(
            "INPUT_WAIT_BUDGET_EXCEEDED",
            "timeoutSeconds exceeds the run's remaining input-wait budget.",
            remainingSeconds=int(max(0, remaining)),
        )
    if existing is not None:  # reopen for the new attempt
        existing.state = "pending"
        existing.attempt = attempt_no
        existing.title = title
        existing.prompt = prompt
        existing.schema = schema
        existing.preview = redact(preview) if preview else None
        existing.deadline = now + timedelta(seconds=timeout_seconds)
        existing.version += 1
        await append_event(
            session,
            run,
            "run.input_requested",
            {"inputRequestId": str(existing.id), "key": key, "title": title, "state": "pending"},
        )
        if run.state == RunState.RUNNING:
            await transition(session, run, RunState.WAITING_INPUT, reason=f"input:{key}")
        return existing
    request = InputRequest(
        run_id=run_id,
        key=key,
        attempt=attempt_no,
        title=title,
        prompt=prompt,
        schema=schema,
        preview=redact(preview) if preview else None,
        state="pending",
        deadline=now + timedelta(seconds=timeout_seconds),
        version=1,
    )
    session.add(request)
    await session.flush()
    await append_event(
        session,
        run,
        "run.input_requested",
        {"inputRequestId": str(request.id), "key": key, "title": title, "state": "pending"},
    )
    if run.state == RunState.RUNNING:
        await transition(session, run, RunState.WAITING_INPUT, reason=f"input:{key}")
    return request


async def answer(
    session: AsyncSession,
    request_id: uuid.UUID,
    *,
    version: int,
    value: Any,
    user_id: uuid.UUID,
) -> InputRequest:
    # Lock order is always run row, then request row.
    run_id = await session.scalar(select(InputRequest.run_id).where(InputRequest.id == request_id))
    if run_id is None:
        raise not_found("Input request", request_id)
    run = await lock_run(session, run_id)
    request = await session.scalar(
        select(InputRequest)
        .where(InputRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert request is not None
    if request.state != "pending":
        raise conflict("INPUT_ALREADY_CLOSED", f"This request is already {request.state}.")
    if request.version != version:
        raise conflict(
            "VERSION_CONFLICT", "The request changed; reload it.", currentVersion=request.version
        )
    if request.deadline <= utcnow():
        raise conflict("INPUT_EXPIRED", "This request has expired.")
    check_size(value, MAX_ANSWER_BYTES, "ANSWER_TOO_LARGE", "The answer")
    errors = [e.message for e in Draft202012Validator(request.schema).iter_errors(value)][:10]
    if errors:
        raise invalid(
            "INVALID_ANSWER", "The answer does not match the requested form.", errors=errors
        )
    request.state = "answered"
    request.answer = value
    request.answered_by = user_id
    request.answered_at = utcnow()
    request.version += 1
    await append_event(
        session,
        run,
        "run.input_answered",
        {"inputRequestId": str(request.id), "key": request.key, "state": "answered"},
    )
    await _resume_if_no_pending(session, run)
    return request


async def expire_inputs(session: AsyncSession, run: AgentRun, now: datetime) -> int:
    pending = (
        await session.scalars(
            select(InputRequest).where(
                InputRequest.run_id == run.id,
                InputRequest.state == "pending",
                InputRequest.deadline <= now,
            )
        )
    ).all()
    for request in pending:
        request.state = "expired"
        request.version += 1
        await append_event(
            session,
            run,
            "run.input_closed",
            {"inputRequestId": str(request.id), "key": request.key, "state": "expired"},
        )
    if pending:
        await _resume_if_no_pending(session, run)
    return len(pending)


async def _resume_if_no_pending(session: AsyncSession, run: AgentRun) -> None:
    if run.state != RunState.WAITING_INPUT:
        return
    await session.flush()
    remaining = await session.scalar(
        select(func.count())
        .select_from(InputRequest)
        .where(InputRequest.run_id == run.id, InputRequest.state == "pending")
    )
    if not remaining:
        await transition(session, run, RunState.RUNNING, reason="input_closed")


async def close_pending_inputs(session: AsyncSession, run: AgentRun, state: str) -> None:
    pending = (
        await session.scalars(
            select(InputRequest).where(
                InputRequest.run_id == run.id, InputRequest.state == "pending"
            )
        )
    ).all()
    for request in pending:
        request.state = state
        request.version += 1
        await append_event(
            session,
            run,
            "run.input_closed",
            {"inputRequestId": str(request.id), "key": request.key, "state": state},
        )


# --- Agent idempotency keys -----------------------------------------------------


async def claim_action(
    session: AsyncSession, run_id: uuid.UUID, attempt_no: int, key: str
) -> dict[str, Any]:
    """Claim an external-action key for this run.

    Only the call that creates the key receives ``claimed`` (proceed with the side
    effect). Any later claim of an uncompleted key, from this attempt or another,
    receives ``in_doubt``: the side effect may already have happened, so the agent
    must check provider state before acting. ``completed`` returns the stored result.
    """
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    existing = await session.scalar(
        select(IdempotencyAction).where(
            IdempotencyAction.run_id == run_id, IdempotencyAction.key == key
        )
    )
    if existing is None:
        session.add(IdempotencyAction(run_id=run_id, key=key, state="claimed", attempt=attempt_no))
        await session.flush()
        return {"key": key, "status": "claimed"}
    if existing.state == "completed":
        return {"key": key, "status": "completed", "result": existing.result}
    existing.state = "in_doubt"
    existing.attempt = attempt_no
    return {"key": key, "status": "in_doubt"}


async def complete_action(
    session: AsyncSession, run_id: uuid.UUID, attempt_no: int, key: str, result: Any
) -> dict[str, Any]:
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    action = await session.scalar(
        select(IdempotencyAction)
        .where(IdempotencyAction.run_id == run_id, IdempotencyAction.key == key)
        .with_for_update()
    )
    if action is None:
        raise conflict("ACTION_NOT_CLAIMED", "Claim the action key before completing it.")
    if action.state == "completed":
        return {"key": key, "status": "completed", "result": action.result}
    action.state = "completed"
    action.result = redact(result)
    action.completed_at = utcnow()
    return {"key": key, "status": "completed", "result": action.result}


def ensure(condition: bool, error: PlatformError) -> None:
    if not condition:
        raise error
