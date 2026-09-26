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
from crewquarters_shared.schema_guard import check_schema, check_size, json_size
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


async def _last_sequence(session: AsyncSession, run_id: uuid.UUID) -> int:
    last = await session.scalar(
        select(func.coalesce(func.max(RunEvent.sequence), 0)).where(RunEvent.run_id == run_id)
    )
    return int(last or 0)


async def append_event(
    session: AsyncSession, run: AgentRun, event_type: str, payload: dict[str, Any]
) -> RunEvent:
    """Append an event. The caller must hold the run row lock."""
    event = RunEvent(
        run_id=run.id,
        sequence=await _last_sequence(session, run.id) + 1,
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
    parent_run_id: uuid.UUID | None = None,
    start_key: str | None = None,
    trigger_input: dict[str, Any] | None = None,
) -> NewRun:
    """Create a QUEUED run and its first dispatch job.

    Scheduled runs are inserted with ``ON CONFLICT DO NOTHING`` against the unique
    ``(schedule_id, scheduled_for)`` index, which is the final duplicate defense. Runs an agent
    starts do the same against ``(parent_run_id, start_key)``, so a retried start returns the
    run the first attempt created.
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
        "parent_run_id": parent_run_id,
        "start_key": start_key,
        "trigger_input": trigger_input,
    }
    stmt = pg_insert(AgentRun).values(**values)
    duplicate = None
    if schedule_id is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["schedule_id", "scheduled_for"],
            index_where=AgentRun.schedule_id.isnot(None),
        )
        duplicate = (AgentRun.schedule_id == schedule_id, AgentRun.scheduled_for == scheduled_for)
    elif parent_run_id is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["parent_run_id", "start_key"],
            index_where=AgentRun.parent_run_id.isnot(None),
        )
        duplicate = (AgentRun.parent_run_id == parent_run_id, AgentRun.start_key == start_key)
    inserted = await session.scalar(stmt.returning(AgentRun.id))
    if inserted is None:
        assert duplicate is not None
        existing = await session.scalar(select(AgentRun).where(*duplicate))
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


@dataclass(frozen=True)
class AgentEventInput:
    client_event_id: str
    type: str
    payload: dict[str, Any]
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class EventBatchResult:
    accepted: int
    duplicates: int
    last_sequence: int


def _event_rejection(
    index: int, event: AgentEventInput, run: AgentRun, validator: Draft202012Validator | None
) -> dict[str, Any] | None:
    base = {"index": index, "clientEventId": event.client_event_id}
    if event.type not in AGENT_EVENT_TYPES:
        return {
            **base,
            "code": "INVALID_EVENT_TYPE",
            "errors": [f"Agents cannot emit {event.type}."],
        }
    if json_size(event.payload) > MAX_EVENT_BYTES:
        return {
            **base,
            "code": "EVENT_TOO_LARGE",
            "errors": [f"The event payload is larger than {MAX_EVENT_BYTES // 1024} KiB."],
        }
    if validator is None:
        return None
    envelope = {
        "runId": str(run.id),
        "sequence": 1,
        "attempt": run.current_attempt,
        "type": event.type,
        "payload": event.payload,
        "createdAt": (event.occurred_at or utcnow()).isoformat(),
    }
    errors = [e.message for e in validator.iter_errors(envelope)][:5]
    return {**base, "code": "INVALID_EVENT", "errors": errors} if errors else None


async def record_agent_event_batch(
    session: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    events: list[AgentEventInput],
    *,
    validator: Draft202012Validator | None = None,
) -> EventBatchResult:
    """Store a batch of agent events all-or-nothing.

    Every event is validated before anything is written. If any is rejected, nothing is
    stored and a ``422`` lists every rejected event, so a client that then resends event
    by event stores each valid event exactly once. A ``clientEventId`` already stored for
    this run (or repeated in the batch) is skipped, so a retried batch is a no-op.
    """
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    if RunState(run.state) in TERMINAL_STATES:
        raise conflict("RUN_FINISHED", "The run has already finished.")
    rejected = [
        r for i, e in enumerate(events) if (r := _event_rejection(i, e, run, validator)) is not None
    ]
    if rejected:
        codes = {r["code"] for r in rejected}
        raise invalid(
            codes.pop() if len(codes) == 1 else "INVALID_EVENT",
            f"{len(rejected)} of {len(events)} events were rejected; none were stored.",
            rejected=rejected,
        )
    ids = [e.client_event_id for e in events]
    stored = set(
        (
            await session.scalars(
                select(RunEvent.client_event_id).where(
                    RunEvent.run_id == run.id, RunEvent.client_event_id.in_(ids)
                )
            )
        ).all()
    )
    sequence = await _last_sequence(session, run.id)
    accepted = duplicates = 0
    now = utcnow()
    for event in events:
        if event.client_event_id in stored:
            duplicates += 1
            continue
        stored.add(event.client_event_id)
        sequence += 1
        accepted += 1
        session.add(
            RunEvent(
                run_id=run.id,
                sequence=sequence,
                attempt=run.current_attempt,
                type=event.type,
                payload=redact(event.payload),
                created_at=now,
                client_event_id=event.client_event_id,
                occurred_at=event.occurred_at,
            )
        )
    await session.flush()
    return EventBatchResult(accepted=accepted, duplicates=duplicates, last_sequence=sequence)


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
    details: dict[str, Any] | None = None,
) -> None:
    """Platform-side failure (timeout, crash, dead job). Enqueues a container stop."""
    target = RunState.INTERRUPTED if interrupted else RunState.FAILED
    if RunState(run.state) in TERMINAL_STATES or not can_transition(run.state, target):
        return
    error: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if details:
        error["details"] = details
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


# States in which a container exit without a result fails the run. CANCELLING is left to the
# cancel job, and terminal runs only get the exit code recorded.
EXIT_FAILS_STATES = frozenset(
    {RunState.PREPARING, RunState.LOADING_MODEL, RunState.RUNNING, RunState.WAITING_INPUT}
)
AGENT_OUT_OF_MEMORY = "AGENT_OUT_OF_MEMORY"
AGENT_EXITED = "AGENT_EXITED"
AGENT_EXITED_WITHOUT_RESULT = "AGENT_EXITED_WITHOUT_RESULT"
SIGKILL_EXIT_CODE = 137  # 128 + SIGKILL, as the container's init reports it


async def record_container_exit(
    session: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    runtime_ref: str,
    *,
    exit_code: int | None,
    oom_killed: bool,
    memory_limit_bytes: int | None = None,
) -> str | None:
    """The runtime saw the attempt's container exit (ADR 0006, revision 1).

    Records the exit code on the attempt. If the run is still active on this attempt, the
    agent died without posting a result, so the run fails (retryable) with
    ``AGENT_OUT_OF_MEMORY``, ``AGENT_EXITED`` or ``AGENT_EXITED_WITHOUT_RESULT``, and the error
    code is returned. The SDK posts its result and waits for the answer before the process
    exits, so a result is always committed before its container's exit can be observed; the
    caller must observe the exit *before* calling this, and a run that already succeeded is
    left alone.
    """
    run = await lock_run(session, run_id)
    attempt = await get_attempt(session, run_id, attempt_no)
    if attempt is None or attempt.runtime_ref != runtime_ref:
        return None
    if attempt.exit_code is None and exit_code is not None:
        attempt.exit_code = exit_code
    if run.current_attempt != attempt_no or run.state not in EXIT_FAILS_STATES:
        return None
    details: dict[str, Any] = {"exitCode": exit_code, "oomKilled": oom_killed}
    # Docker sometimes loses the OOM flag: the kernel's kill and the exit race, and about 1 in
    # 12 OOM kills reports OOMKilled=false with exit code 137 (Docker 29, cgroup v2). The
    # platform stops a container only after its run has left these states, and apart from a
    # Docker or host shutdown nothing else SIGKILLs an agent, so an unflagged SIGKILL is
    # reported as a probable out-of-memory kill (details.oomKilled stays false).
    if oom_killed or exit_code == SIGKILL_EXIT_CODE:
        memory_mb = await _memory_limit_mb(session, run, memory_limit_bytes)
        details["memoryLimitMb"] = memory_mb
        limit = f"its {memory_mb} MiB memory limit" if memory_mb else "its memory limit"
        code = AGENT_OUT_OF_MEMORY
        if oom_killed:
            message = (
                f"The agent ran out of memory and was killed: it reached {limit} "
                f"(exit code {exit_code})."
            )
        else:
            message = (
                f"The agent was killed (SIGKILL, exit code {exit_code}) without reporting a "
                f"result, most likely for reaching {limit}. The runtime did not confirm the "
                "out-of-memory kill."
            )
    elif exit_code == 0:
        code = AGENT_EXITED_WITHOUT_RESULT
        message = "The agent exited (exit code 0) without reporting a result."
    else:
        code = AGENT_EXITED
        stage = " before its handshake" if run.state == RunState.PREPARING else ""
        message = f"The agent exited with code {exit_code}{stage} without reporting a result."
    # Retryable, like HEARTBEAT_LOST before it: the owner decides whether to retry.
    await fail_run(session, run, code, message, retryable=True, details=details)
    return code


async def _memory_limit_mb(
    session: AsyncSession, run: AgentRun, memory_limit_bytes: int | None
) -> int | None:
    if memory_limit_bytes:
        return memory_limit_bytes // (1024 * 1024)
    version = await session.get(AgentVersion, run.agent_version_id)
    resources = ((version.manifest if version else {}).get("spec") or {}).get("resources") or {}
    memory = resources.get("memoryMb")
    return int(memory) if isinstance(memory, int | float) else None


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
    session: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    key: str,
    claim_token: str | None = None,
) -> dict[str, Any]:
    """Claim an external-action key for this run.

    Only the call that creates the key receives ``claimed`` (proceed with the side
    effect). A retry of that same call (the same ``claim_token`` from the same attempt,
    resent because the response was lost) receives ``claimed`` again: it is the same
    claimant and nothing else has touched the key. Any other later claim of an
    uncompleted key, from this attempt or another, receives ``in_doubt``: the side effect
    may already have happened, so the agent must check provider state before acting.
    ``completed`` returns the stored result.
    """
    run = await lock_run(session, run_id)
    require_attempt(run, attempt_no)
    existing = await session.scalar(
        select(IdempotencyAction).where(
            IdempotencyAction.run_id == run_id, IdempotencyAction.key == key
        )
    )
    if existing is None:
        session.add(
            IdempotencyAction(
                run_id=run_id,
                key=key,
                state="claimed",
                attempt=attempt_no,
                claim_token=claim_token,
            )
        )
        await session.flush()
        return {"key": key, "status": "claimed"}
    if existing.state == "completed":
        return {"key": key, "status": "completed", "result": existing.result}
    if (
        existing.state == "claimed"
        and claim_token is not None
        and existing.claim_token == claim_token
        and existing.attempt == attempt_no
    ):
        return {"key": key, "status": "claimed"}
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
