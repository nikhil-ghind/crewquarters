"""contract-probe: runs independent checks of every broker capability the SDK exposes."""

from __future__ import annotations

import time

from contract_probe.checks import REGISTRY, Ctx, ordered, summarize, wait_for_cancellation
from contract_probe.models import CheckResult, ProbeConfig, ProbeReport
from crewquarters import Agent
from crewquarters.errors import PlatformError

agent = Agent("contract-probe", config_model=ProbeConfig, result_model=ProbeReport)


@agent.run
async def run(ctx: Ctx) -> ProbeReport:
    results: list[CheckResult] = []
    for name in ordered(list(ctx.config.checks)):
        if name == "cancellation":
            results.append(await wait_for_cancellation(ctx))
            continue
        started = time.monotonic()
        try:
            status, detail = await REGISTRY[name](ctx)
        except PlatformError as exc:
            status, detail = "failed", f"{exc.code}: {exc}"
        results.append(
            CheckResult(
                name=name, status=status, detail=detail, duration_ms=int((time.monotonic() - started) * 1000)
            )
        )
        await ctx.events.log(
            "info" if status != "failed" else "error", f"check {name}: {status}", detail=detail
        )
    return summarize(results)
