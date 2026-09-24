"""Prometheus metrics (PLAN.md section 15.1). Each service owns a private registry so
several apps in one process (tests) never collide."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class ApiMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "cq_http_requests_total",
            "HTTP requests by route template, method, and status.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "cq_http_request_duration_seconds",
            "HTTP request latency (excluding streamed bodies).",
            ["method", "route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2.5, 5),
            registry=self.registry,
        )
        self.jobs = Gauge(
            "cq_jobs", "Jobs by state and type.", ["state", "type"], registry=self.registry
        )
        self.oldest_job = Gauge(
            "cq_job_oldest_available_age_seconds",
            "Age of the oldest job waiting to be claimed.",
            registry=self.registry,
        )
        self.runs = Gauge("cq_runs", "Runs by state.", ["state"], registry=self.registry)
        self.pending_inputs = Gauge(
            "cq_input_requests_pending", "Unanswered Crew Requests.", registry=self.registry
        )
        self.dispatch_lag = Gauge(
            "cq_schedule_dispatch_lag_seconds",
            "Delay between the scheduled time and run creation for the latest scheduled run.",
            registry=self.registry,
        )

    async def collect_database(self, db: AsyncSession) -> None:
        self.jobs.clear()
        for state, job_type, count in (
            await db.execute(text("SELECT state, type, count(*) FROM jobs GROUP BY state, type"))
        ).all():
            self.jobs.labels(state, job_type).set(count)
        oldest = await db.scalar(
            text(
                "SELECT coalesce(extract(epoch FROM now() - min(available_at)), 0) "
                "FROM jobs WHERE state = 'available' AND available_at <= now()"
            )
        )
        self.oldest_job.set(float(oldest or 0))
        self.runs.clear()
        for state, count in (
            await db.execute(text("SELECT state, count(*) FROM agent_runs GROUP BY state"))
        ).all():
            self.runs.labels(state).set(count)
        self.pending_inputs.set(
            float(
                await db.scalar(text("SELECT count(*) FROM input_requests WHERE state = 'pending'"))
                or 0
            )
        )
        lag = await db.scalar(
            text(
                "SELECT extract(epoch FROM created_at - scheduled_for) FROM agent_runs "
                "WHERE trigger = 'schedule' ORDER BY created_at DESC LIMIT 1"
            )
        )
        self.dispatch_lag.set(float(lag or 0))

    def render(self) -> bytes:
        return generate_latest(self.registry)


class SchedulerMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.leader = Gauge(
            "cq_scheduler_leader", "1 while this process holds leadership.", registry=self.registry
        )
        self.ticks = Counter(
            "cq_scheduler_ticks_total", "Scheduler ticks run as leader.", registry=self.registry
        )
        self.fired = Counter(
            "cq_schedules_fired_total",
            "Schedule evaluations by outcome.",
            ["outcome"],
            registry=self.registry,
        )
        self.jobs = Counter(
            "cq_jobs_processed_total",
            "Jobs executed by type and outcome.",
            ["type", "outcome"],
            registry=self.registry,
        )
        self.reconciled = Counter(
            "cq_reconciler_actions_total",
            "Reconciler actions by kind.",
            ["action"],
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
