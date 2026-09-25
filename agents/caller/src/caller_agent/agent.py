"""caller: consenting Sheets rows → operator approval → fixed-script calls → results to Sheets."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from caller_agent.approval import build_request, personalised
from caller_agent.config import CallerConfig
from caller_agent.models import CallerResult, RowResult, Summary
from caller_agent.results import FAILED_STATUSES, HEADER, display_status, row_values
from caller_agent.rows import ContactRow, Plan, classify, read_rows
from crewquarters import Agent, RunContext
from crewquarters.errors import (
    AgentError,
    NeedsConnection,
    OutcomeUnknown,
    PlatformError,
    ProviderError,
)
from crewquarters.redact import mask_phone
from crewquarters.telephony import Call

agent = Agent("caller", config_model=CallerConfig, result_model=CallerResult)
Ctx = RunContext[CallerConfig]
WRITE_ATTEMPTS = 3


def _skipped_rows(plan: Plan, contacts: list[ContactRow]) -> list[RowResult]:
    phones = {c.row: c.phone for c in contacts}
    return [
        RowResult(
            row=s.row,
            name=s.name,
            phone_masked=mask_phone(phones[s.row]),
            consent="skipped",
            skip_reason=s.reason,
        )
        for s in plan.skipped
    ]


def _summary(rows: list[RowResult], skipped: int) -> Summary:
    called = [r for r in rows if r.call_sid]
    return Summary(
        called=len(called),
        answered=sum(1 for r in rows if (r.call_status or "").startswith("answered")),
        responses_captured=sum(1 for r in rows if r.call_status == "answered_speech"),
        skipped=skipped,
        failed=sum(
            1 for r in rows if r.consent == "validated" and r.call_status in FAILED_STATUSES
        ),
    )


async def _write(ctx: Ctx, range_: str, values: list[str]) -> str:
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            await ctx.google.sheets.update_values(ctx.config.spreadsheet_id, range_, [list(values)])
            return "written"
        except (ProviderError, OutcomeUnknown) as exc:
            await ctx.events.log(
                "warning", f"sheet write to {range_} failed (attempt {attempt}): {exc.code}"
            )
            if attempt < WRITE_ATTEMPTS:
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
    return "failed"


async def _call(ctx: Ctx, contact: ContactRow) -> tuple[Call | None, str | None]:
    key = f"call:{ctx.run.id}:{contact.row}"
    config = ctx.config

    async def create() -> Call:
        return await ctx.telephony.create_call(
            contact.phone,
            disclosure=config.disclosure,
            script=personalised(config.script, contact.name),
            gather_seconds=config.response_seconds,
            idempotency_key=key,
        )

    try:
        # Resuming an in-doubt claim is safe: the broker returns the existing call for the same key.
        call = await ctx.idempotency.once(key, create, result_type=Call, resume_in_progress=True)
        final = await ctx.telephony.wait_for_call(
            call.id,
            timeout_seconds=config.call_timeout_seconds,
            poll_seconds=config.call_poll_seconds,
        )
    except PlatformError as exc:
        await ctx.events.log("error", f"call for row {contact.row} failed: {exc.code}")
        return None, f"{exc.code}: {exc}"
    return final, None


@agent.run
async def run(ctx: Ctx) -> CallerResult:
    config = ctx.config
    tz = ZoneInfo(config.timezone)
    await ctx.events.progress(5, "Reading contacts", step="read")
    try:
        values = await ctx.google.sheets.get_values(config.spreadsheet_id, config.input_range)
    except NeedsConnection as exc:
        raise AgentError(
            "Google access has expired or is missing; reconnect Google in Connections",
            code="GOOGLE_RECONNECT_REQUIRED",
        ) from exc
    contacts = read_rows(values, config.input_start_row)
    plan = classify(contacts, config.max_calls)
    skipped = _skipped_rows(plan, contacts)
    await ctx.events.log("info", f"{len(plan.eligible)} eligible, {len(plan.skipped)} skipped")
    if not plan.eligible:
        return CallerResult(
            operator_decision="not_required", summary=_summary(skipped, len(skipped)), rows=skipped
        )

    request = build_request(plan, config)
    await ctx.events.progress(15, "Waiting for your approval", step="approve")
    answer = await ctx.input.ask(
        request["key"],
        request["title"],
        request["prompt"],
        choices=request["choices"],
        preview=request["preview"],
        consequence=request["consequence"],
        timeout_seconds=max(1, min(3600, int(ctx.input.remaining_wait_seconds))),
    )
    pending = [
        RowResult(row=c.row, name=c.name, phone_masked=mask_phone(c.phone), consent="validated")
        for c in plan.eligible
    ]
    if answer.value != "approve":
        await ctx.events.log("info", "operator cancelled; no calls placed")
        rows = sorted(pending + skipped, key=lambda r: r.row)
        return CallerResult(
            operator_decision="cancelled", summary=_summary(rows, len(skipped)), rows=rows
        )

    await _write(ctx, f"{config.result_tab}!A1:H1", HEADER)
    called: list[RowResult] = []
    for index, contact in enumerate(plan.eligible):
        await ctx.events.progress(
            20 + 75 * index / len(plan.eligible),
            f"Calling row {contact.row} ({mask_phone(contact.phone)})",
            step="call",
        )
        call, error = await _call(ctx, contact)
        status = display_status(call)
        completed_at = (
            datetime.now(tz).isoformat(timespec="seconds")
            if call is not None and call.terminal
            else None
        )
        if call is not None:
            error = call.error_code or (
                "call still active at the timeout" if status == "timeout" else None
            )
        cells = row_values(contact, call, status, completed_at, error)
        sheet_write = await _write(ctx, f"{config.result_tab}!A{contact.row}:H{contact.row}", cells)
        called.append(
            RowResult(
                row=contact.row,
                name=contact.name,
                phone_masked=mask_phone(contact.phone),
                consent="validated",
                call_status=status,
                call_sid=call.id if call else None,
                transcript=call.transcript if call else None,
                sheet_write="written" if sheet_write == "written" else "failed",
                completed_at=completed_at,
                error=error,
            )
        )
    rows = sorted(called + skipped, key=lambda r: r.row)
    await ctx.events.progress(100, "Calls finished", step="done")
    return CallerResult(
        operator_decision="approved", summary=_summary(rows, len(skipped)), rows=rows
    )
