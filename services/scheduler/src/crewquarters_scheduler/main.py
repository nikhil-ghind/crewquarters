"""Scheduler process: job workers everywhere, scheduler + reconciler on the leader.

Leadership is a PostgreSQL session advisory lock held on a dedicated connection.
If that connection drops, the lock is released and another process takes over.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from crewquarters_scheduler import exits, reconciler, scheduler
from crewquarters_scheduler.worker import Worker
from crewquarters_shared.config import Settings, get_settings
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.fakes.runtime import FakeRuntime
from crewquarters_shared.logs import configure_logging
from crewquarters_shared.metrics import CONTENT_TYPE, SchedulerMetrics
from crewquarters_shared.runtime import DaemonRuntimeClient, RuntimeAdapter
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger("crewquarters.scheduler")

LEADER_LOCK_KEY = 0x43515343  # "CQSC"


def build_runtime(settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> RuntimeAdapter:
    if settings.runtime_adapter == "daemon":
        return DaemonRuntimeClient(
            settings.runtime_socket, settings.internal_service_token.get_secret_value()
        )
    return FakeRuntime(sessions, heartbeat_seconds=settings.heartbeat_timeout_seconds)


async def leader_loop(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    stop: asyncio.Event,
    metrics: SchedulerMetrics,
    runtime: RuntimeAdapter,
) -> None:
    while not stop.is_set():
        try:
            async with engine.connect() as lock_conn:
                acquired = await lock_conn.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": LEADER_LOCK_KEY}
                )
                if not acquired:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=5)
                    continue
                log.info("acquired scheduler leadership", extra={"event": "leader.acquired"})
                metrics.leader.set(1)
                watcher = asyncio.create_task(
                    exit_watch_loop(sessions, runtime, settings, metrics), name="exit-watch"
                )
                try:
                    await _lead(lock_conn, sessions, settings, stop, metrics)
                finally:
                    watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher
                    metrics.leader.set(0)
        except Exception:
            log.exception("leader loop error; retrying", extra={"event": "leader.error"})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2)


async def _lead(
    lock_conn: AsyncConnection,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    stop: asyncio.Event,
    metrics: SchedulerMetrics,
) -> None:
    last_reconcile = 0.0
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        async with sessions() as session, session.begin():
            fired = await scheduler.tick(session, utcnow(), settings.misfire_grace_seconds)
        metrics.ticks.inc()
        for result in fired:
            metrics.fired.labels(result.outcome).inc()
            log.info(
                "schedule %s for %s",
                result.outcome,
                result.scheduled_for.isoformat(),
                extra={"event": f"schedule.{result.outcome}", "run_id": result.run_id},
            )
        if loop.time() - last_reconcile >= settings.reconciler_interval_seconds:
            async with sessions() as session, session.begin():
                report = await reconciler.tick(session, utcnow())
            for action in (
                "dead_jobs",
                "active_timeouts",
                "input_timeouts",
                "interrupted",
                "expired_inputs",
            ):
                count = getattr(report, action)
                if count:
                    metrics.reconciled.labels(action).inc(count)
            if report.requeued_jobs:
                metrics.reconciled.labels("requeued_jobs").inc(len(report.requeued_jobs))
            last_reconcile = loop.time()
        # Keep the lock connection alive and detect loss of leadership.
        await lock_conn.execute(text("SELECT 1"))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_tick_seconds)


async def exit_watch_loop(
    sessions: async_sessionmaker[AsyncSession],
    runtime: RuntimeAdapter,
    settings: Settings,
    metrics: SchedulerMetrics,
) -> None:
    """While leading: fail runs whose agent container exited (``exits.tick``). A separate task,
    so a slow runtime call never delays schedules or the reconciler."""
    while True:
        try:
            report = await exits.tick(sessions, runtime, utcnow())
            for code, count in report.failed.items():
                metrics.reconciled.labels(f"exited:{code}").inc(count)
        except Exception:
            log.exception("exit watch failed", extra={"event": "exit_watch.error"})
        await asyncio.sleep(settings.exit_watch_interval_seconds)


async def serve_metrics(settings: Settings, metrics: SchedulerMetrics) -> asyncio.Server:
    """Minimal HTTP endpoint: ``GET /metrics`` (Prometheus text) and ``GET /health``."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            path = request_line.decode(errors="replace").split(" ")[1] if request_line else ""
            if path == "/metrics":
                status, body, ctype = "200 OK", metrics.render(), CONTENT_TYPE
            elif path == "/health":
                status, body, ctype = "200 OK", b'{"status":"ok"}', "application/json"
            else:
                status, body, ctype = "404 Not Found", b"", "text/plain"
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (TimeoutError, IndexError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(
        handle, settings.scheduler_metrics_host, settings.scheduler_metrics_port
    )


async def serve(settings: Settings) -> None:
    engine = create_engine(settings.database_url, settings.db_pool_size)
    sessions = session_factory(engine)
    runtime = build_runtime(settings, sessions)
    metrics = SchedulerMetrics()
    worker = Worker(sessions, runtime, settings, metrics=metrics)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    server = await serve_metrics(settings, metrics)
    log.info(
        "scheduler starting (worker %s, runtime %s)",
        worker.worker_id,
        settings.runtime_adapter,
        extra={"event": "scheduler.start"},
    )
    try:
        await asyncio.gather(
            worker.run_forever(stop),
            leader_loop(engine, sessions, settings, stop, metrics, runtime),
        )
    finally:
        server.close()
        await runtime.close()
        await engine.dispose()


def main() -> None:
    configure_logging("scheduler")
    asyncio.run(serve(get_settings()))


if __name__ == "__main__":
    main()
