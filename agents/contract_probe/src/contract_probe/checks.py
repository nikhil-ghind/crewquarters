"""Independent contract checks. Each returns (status, detail); platform errors fail only that check."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from contract_probe.models import CheckResult, ProbeAnswer, ProbeConfig, ProbeReport, Status
from crewquarters import RunContext
from crewquarters.errors import AgentError, PermissionDenied

Ctx = RunContext[ProbeConfig]
Check = Callable[[Ctx], Awaitable[tuple[Status, str]]]
EGRESS_TARGETS = [("1.1.1.1", 443), ("host.docker.internal", 80), ("example.com", 443)]


async def check_handshake(ctx: Ctx) -> tuple[Status, str]:
    run = ctx.run
    if not (run.id and run.installation_id and run.agent_version and run.attempt >= 1):
        return "failed", "handshake is missing run fields"
    if run.trigger == "schedule" and run.scheduled_for is None:
        return "failed", "scheduled run has no scheduledFor"
    scheduled = f" scheduled for {run.scheduled_for.isoformat()}" if run.scheduled_for else ""
    return "passed", f"run {run.id} attempt {run.attempt} trigger {run.trigger}{scheduled}"


async def check_events(ctx: Ctx) -> tuple[Status, str]:
    for index in range(60):
        await ctx.events.log("debug", f"probe event {index}", index=index)
    for percent in (0, 50, 100):
        await ctx.events.progress(percent, f"probe progress {percent}%", step="events")
    await ctx.events.flush()
    return "passed", "60 log events and 3 progress events delivered"


async def check_input(ctx: Ctx) -> tuple[Status, str]:
    answer = await ctx.input.ask(
        "probe-input-v1",
        "Contract probe input check",
        "Choose ok to confirm the input round trip works.",
        choices=["ok", "fail"],
        timeout_seconds=min(300, max(1, int(ctx.input.remaining_wait_seconds))),
    )
    return (
        ("passed", "operator answered ok")
        if answer.value == "ok"
        else ("failed", f"answer was {answer.value}")
    )


async def check_llm(ctx: Ctx) -> tuple[Status, str]:
    result = await ctx.llm.chat("local.general", [{"role": "user", "content": "Reply with the word pong."}])
    if not result.text.strip():
        return "failed", "empty completion"
    if result.locality != "local":
        return "failed", f"local profile answered from {result.locality}"
    return "passed", f"{result.provider}/{result.model} answered locally"


async def check_structured(ctx: Ctx) -> tuple[Status, str]:
    result = await ctx.llm.chat(
        "local.general",
        [{"role": "user", "content": "Return JSON with an answer field set to pong."}],
        response_model=ProbeAnswer,
        idempotency_key="probe-structured-v1",
    )
    return "passed", f"parsed answer={result.parsed.answer!r}"


async def check_knowledge(ctx: Ctx) -> tuple[Status, str]:
    kb = ctx.config.knowledge_base_id
    if not kb:
        return "failed", "knowledgeBaseId is not configured"
    result = await ctx.knowledge.search(kb, "contract probe knowledge search", top_k=3)
    if not result.passages or not result.passages[0].citation_id:
        return "failed", "no cited passages returned"
    return "passed", f"{len(result.passages)} passages, top citation {result.passages[0].citation_id}"


async def check_idempotency(ctx: Ctx) -> tuple[Status, str]:
    calls = 0

    async def action() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"n": calls}

    first = await ctx.idempotency.once("probe-once-v1", action)
    second = await ctx.idempotency.once("probe-once-v1", action)
    if calls == 1 and first == second:
        return "passed", "the guarded action ran exactly once"
    return "failed", f"action ran {calls} times"


async def check_permissions(ctx: Ctx) -> tuple[Status, str]:
    try:
        await ctx.google.gmail.list_message_ids("")
    except PermissionDenied:
        return "passed", "undeclared gmail access was denied"
    return "failed", "undeclared gmail access was allowed"


async def _connects(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


def _filesystem_problems() -> list[str]:
    problems: list[str] = []
    if os.getuid() == 0:
        problems.append("running as root")
    try:
        Path("/probe-write-test").write_text("x")
        problems.append("root filesystem is writable")
    except OSError:
        pass
    try:
        Path("/tmp/probe-write-test").write_text("x")
    except OSError:
        problems.append("/tmp is not writable")
    return problems


async def check_isolation(ctx: Ctx) -> tuple[Status, str]:
    if not ctx.config.expect_isolation:
        return "skipped", "expectIsolation is false (agent is not running in the hardened container)"
    problems = await asyncio.to_thread(_filesystem_problems)
    for host, port in EGRESS_TARGETS:
        if await _connects(host, port):
            problems.append(f"reached {host}:{port}")
    if problems:
        return "failed", "; ".join(problems)
    return (
        "passed",
        f"uid {os.getuid()}, read-only root, writable /tmp, no egress to {len(EGRESS_TARGETS)} targets",
    )


REGISTRY: dict[str, Check] = {
    "handshake": check_handshake,
    "events": check_events,
    "input": check_input,
    "llm": check_llm,
    "structured": check_structured,
    "knowledge": check_knowledge,
    "idempotency": check_idempotency,
    "permissions": check_permissions,
    "isolation": check_isolation,
}


def ordered(checks: list[str]) -> list[str]:
    return [c for c in checks if c != "cancellation"] + (["cancellation"] if "cancellation" in checks else [])


async def wait_for_cancellation(ctx: Ctx) -> CheckResult:
    """Passes by being cancelled: the CancelledError propagates and the run ends CANCELLED."""
    started = time.monotonic()
    await ctx.events.log("info", "waiting for cancellation")
    await ctx.events.flush()
    await asyncio.sleep(ctx.config.cancel_wait_seconds)
    return CheckResult(
        name="cancellation",
        status="failed",
        detail=f"no cancellation arrived within {ctx.config.cancel_wait_seconds}s",
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def summarize(results: list[CheckResult]) -> ProbeReport:
    report = ProbeReport(checks=results)
    failed = [r.name for r in results if r.status == "failed"]
    if failed:
        raise AgentError(
            f"{len(failed)} contract check(s) failed: {', '.join(failed)}",
            code="CONTRACT_CHECKS_FAILED",
            details=report.model_dump(mode="json", by_alias=True),
        )
    return report
