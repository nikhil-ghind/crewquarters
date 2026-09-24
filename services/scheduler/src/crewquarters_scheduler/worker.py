"""Job worker: claims jobs from PostgreSQL and executes run lifecycle handlers.

Handlers are idempotent. A worker that dies between claim and completion leaves a
lease that expires; another worker reclaims the job and resumes from the durable
run/attempt state without creating a second run record or a second container.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_shared import audit, capability, jobs
from crewquarters_shared.config import Settings
from crewquarters_shared.db.models import AgentInstallation, AgentRun, AgentVersion
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.metrics import SchedulerMetrics
from crewquarters_shared.runs import service
from crewquarters_shared.runs.states import RunState
from crewquarters_shared.runtime import RunSpec, RuntimeAdapter
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class PermanentJobError(Exception):
    """Raised by a handler for failures that retrying cannot fix."""


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime: RuntimeAdapter,
        settings: Settings,
        worker_id: str | None = None,
        metrics: SchedulerMetrics | None = None,
    ) -> None:
        self.metrics = metrics or SchedulerMetrics()
        self.sessions = sessions
        self.runtime = runtime
        self.settings = settings
        self.worker_id = worker_id or default_worker_id()
        self.handlers: dict[str, Handler] = {
            service.JOB_DISPATCH: self.handle_dispatch,
            service.JOB_CANCEL: self.handle_cancel,
            service.JOB_STOP: self.handle_stop,
        }

    # --- Loop ---------------------------------------------------------------

    async def claim(self) -> jobs.ClaimedJob | None:
        async with self.sessions() as session, session.begin():
            return await jobs.claim(
                session, self.worker_id, self.settings.job_lease_seconds, list(self.handlers)
            )

    async def run_once(self) -> bool:
        job = await self.claim()
        if job is None:
            return False
        await self.execute(job)
        return True

    async def execute(self, job: jobs.ClaimedJob) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        context = {"job_id": job.id, "job_type": job.type, "run_id": job.payload.get("runId")}
        outcome = "succeeded"
        try:
            await self.handlers[job.type](job.payload)
        except PermanentJobError as exc:
            outcome = "failed"
            await self._fail(job, {"code": "PERMANENT", "message": str(exc)}, retryable=False)
        except PlatformError as exc:
            outcome = "failed"
            retryable = exc.status_code >= 500
            await self._fail(job, {"code": exc.code, "message": exc.message}, retryable=retryable)
        except Exception as exc:
            outcome = "failed"
            log.exception("job failed", extra={**context, "event": "job.failed"})
            await self._fail(
                job, {"code": "HANDLER_ERROR", "message": type(exc).__name__}, retryable=True
            )
        else:
            async with self.sessions() as session, session.begin():
                await jobs.complete(session, job.id, self.worker_id)
        finally:
            self.metrics.jobs.labels(job.type, outcome).inc()
            log.info("job %s", outcome, extra={**context, "event": f"job.{outcome}"})
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def run_forever(self, stop: asyncio.Event) -> None:
        async def loop() -> None:
            while not stop.is_set():
                try:
                    worked = await self.run_once()
                except Exception:
                    log.exception("worker loop error")
                    worked = False
                if not worked:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=0.25)

        await asyncio.gather(*(loop() for _ in range(self.settings.worker_concurrency)))

    async def _heartbeat(self, job_id: int) -> None:
        interval = max(1.0, self.settings.job_lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            async with self.sessions() as session, session.begin():
                await jobs.heartbeat(
                    session, job_id, self.worker_id, self.settings.job_lease_seconds
                )

    async def _fail(self, job: jobs.ClaimedJob, error: dict[str, Any], *, retryable: bool) -> None:
        async with self.sessions() as session, session.begin():
            state = await jobs.fail(session, job.id, self.worker_id, error, retryable=retryable)
            if state == "dead":
                await on_dead_job(session, job.type, job.payload, error)

    # --- Handlers -----------------------------------------------------------

    async def handle_dispatch(self, payload: dict[str, Any]) -> None:
        """Start the attempt's container.

        The run row is locked only while reading and writing state, never during the
        runtime call: phase 1 moves the run to PREPARING and mints the token, the
        container starts with no lock held (``start_run`` is idempotent on
        ``(run_id, attempt)``), and phase 2 records the runtime reference. If the run
        was cancelled or failed meanwhile, the new container is stopped.
        """
        run_id = uuid.UUID(payload["runId"])
        attempt_no = int(payload["attempt"])
        async with self.sessions() as session, session.begin():
            run = await service.lock_run(session, run_id)
            if run.current_attempt != attempt_no or run.state not in (
                RunState.QUEUED,
                RunState.PREPARING,
            ):
                return  # stale or already past preparation
            installation = await session.get(AgentInstallation, run.installation_id)
            version = await session.get(AgentVersion, run.agent_version_id)
            assert installation is not None and version is not None
            attempt = await service.begin_attempt(
                session, run, self.settings.prepare_timeout_seconds
            )
            if (
                installation.deleted_at is not None
                or installation.needs_reapproval
                or not installation.enabled
            ):
                await service.fail_run(
                    session,
                    run,
                    "INSTALLATION_NOT_READY",
                    "The installation is disabled, removed, or needs permission reapproval.",
                    retryable=False,
                )
                return
            if attempt.runtime_ref is not None:
                return  # container already requested by a previous (crashed) worker
            token, claims = capability.mint(
                signing_key=self.settings.capability_signing_key.get_secret_value(),
                run_id=run.id,
                attempt=attempt_no,
                installation_id=installation.id,
                agent_version_id=version.id,
                capabilities=capability.capabilities_from_permissions(
                    run.permissions_snapshot, run.model_bindings
                ),
                resources={"modelBindings": run.model_bindings},
                ttl_seconds=token_ttl_seconds(run, self.settings),
            )
            attempt.capability_token_id = claims.token_id
            spec = build_spec(run, installation, version, attempt_no, token, self.settings)

        runtime_ref = await self.runtime.start_run(spec)

        stop_now = False
        async with self.sessions() as session, session.begin():
            run = await service.lock_run(session, run_id)
            recorded = await service.get_attempt(session, run_id, attempt_no)
            assert recorded is not None
            if recorded.runtime_ref is None:
                recorded.runtime_ref = runtime_ref
                if run.state == RunState.PREPARING:
                    recorded.heartbeat_expires_at = utcnow() + timedelta(
                        seconds=self.settings.prepare_timeout_seconds
                    )
            if run.current_attempt != attempt_no or run.state in (
                RunState.CANCELLED,
                RunState.FAILED,
                RunState.INTERRUPTED,
            ):
                stop_now = True
        if stop_now:
            await self.runtime.stop_run(runtime_ref, grace_seconds=10)

    async def handle_cancel(self, payload: dict[str, Any]) -> None:
        run_id = uuid.UUID(payload["runId"])
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.state != RunState.CANCELLING:
                return
            attempt = await service.get_attempt(session, run_id, run.current_attempt)
            runtime_ref = attempt.runtime_ref if attempt else None
        if runtime_ref:
            await self.runtime.stop_run(runtime_ref, grace_seconds=10)
        async with self.sessions() as session, session.begin():
            run = await service.lock_run(session, run_id)
            if run.state != RunState.CANCELLING:
                return
            attempt = await service.get_attempt(session, run_id, run.current_attempt)
            if attempt is not None:
                if attempt.runtime_ref and attempt.runtime_ref != runtime_ref:
                    # Dispatch recorded a container after we read the attempt; stop it too.
                    await self.runtime.stop_run(attempt.runtime_ref, grace_seconds=10)
                attempt.state = "cancelled"
                attempt.ended_at = attempt.ended_at or utcnow()
            await service.transition(session, run, RunState.CANCELLED, reason="stopped")

    async def handle_stop(self, payload: dict[str, Any]) -> None:
        run_id = uuid.UUID(payload["runId"])
        async with self.sessions() as session:
            attempt = await service.get_attempt(session, run_id, int(payload["attempt"]))
        if attempt is not None and attempt.runtime_ref:
            await self.runtime.stop_run(attempt.runtime_ref, grace_seconds=10)


def token_ttl_seconds(run: AgentRun, settings: Settings) -> int:
    """A capability token lives as long as the attempt possibly can.

    Both time limits are per attempt, so the attempt cannot outlive preparation +
    active time + input wait. The broker additionally rejects tokens whose run is
    no longer active or whose attempt is not current, which revokes them early.
    """
    return (
        settings.prepare_timeout_seconds
        + run.active_timeout_seconds
        + run.max_input_wait_seconds
        + 300
    )


def build_spec(
    run: AgentRun,
    installation: AgentInstallation,
    version: AgentVersion,
    attempt_no: int,
    token: str,
    settings: Settings,
) -> RunSpec:
    spec_data = version.manifest["spec"]
    return RunSpec(
        run_id=run.id,
        attempt=attempt_no,
        installation_id=installation.id,
        agent_version_id=version.id,
        image=spec_data["image"],
        entrypoint=list(spec_data["entrypoint"]),
        architectures=list(spec_data["architectures"]),
        cpu=float(spec_data["resources"]["cpu"]),
        memory_mb=int(spec_data["resources"]["memoryMb"]),
        pids=int(spec_data["resources"].get("pids", 256)),
        env={
            "PLATFORM_BROKER_URL": settings.broker_url,
            "PLATFORM_RUN_TOKEN": token,
            "PLATFORM_RUN_ID": str(run.id),
            "PLATFORM_ATTEMPT": str(attempt_no),
        },
        config=run.config_snapshot,
    )


async def on_dead_job(
    session: AsyncSession,
    job_type: str,
    payload: dict[str, Any],
    error: dict[str, Any],
    *,
    wait_for_lock: bool = True,
) -> None:
    """Make a dead job operator-visible and settle the run it belonged to.

    The reconciler passes ``wait_for_lock=False`` so one busy run cannot stall the
    whole tick; a run it skips is still caught later by its heartbeat deadline.
    """
    audit.record(
        session,
        action="job.dead",
        actor_type="system",
        target_type="job",
        target_id=payload.get("runId"),
        outcome="failure",
        metadata={"jobType": job_type, "error": error},
    )
    run_id = payload.get("runId")
    if not run_id:
        return
    if wait_for_lock:
        run: AgentRun | None = await service.lock_run(session, uuid.UUID(run_id))
    else:
        run = await service.try_lock_run(session, uuid.UUID(run_id))
    if run is None or run.current_attempt != int(payload.get("attempt", run.current_attempt)):
        return
    if job_type == service.JOB_DISPATCH and run.state in (RunState.QUEUED, RunState.PREPARING):
        if run.state == RunState.QUEUED:
            await service.transition(session, run, RunState.PREPARING)
        await service.fail_run(
            session, run, "DISPATCH_FAILED", "The run could not be started.", retryable=True
        )
    elif job_type == service.JOB_CANCEL and run.state == RunState.CANCELLING:
        await service.transition(
            session,
            run,
            RunState.CANCELLED,
            reason="stop_failed",
            error={"code": "STOP_FAILED", "message": "The container may still be stopping."},
        )
