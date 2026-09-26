"""The voice call center agent: read the sheet, get approval, hold each call, record outcomes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from caller_agent.rows import ContactRow, classify, read_rows
from crewquarters import Agent, RunContext
from crewquarters.errors import OutcomeUnknown, PlatformError
from crewquarters.voice import VoiceCall
from voice_caller.approval import build_request
from voice_caller.config import VoiceCallerConfig
from voice_caller.outcome import (
    CallOutcome,
    classification_messages,
    outcome_for_machine,
    outcome_for_unanswered,
    reconcile,
)
from voice_caller.results import HEADER, STATUS_AFTER, result_range, row_values, status_range
from voice_caller.session import CallReport, CallRunner, LiveKitCallRunner

Cell = str | int | float | bool | None
MACHINE_REASONS = frozenset({"voicemail_reached", "ivr_reached"})
DISPOSITIONS = (
    "completed",
    "declined",
    "dnc",
    "callback",
    "wrong_person",
    "voicemail",
    "no_answer",
    "busy",
    "failed",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


async def _outcome(ctx: RunContext[VoiceCallerConfig], report: CallReport) -> CallOutcome:
    """Turn what happened on the call into a disposition the owner can act on."""
    if not report.answered:
        return outcome_for_unanswered(report.final_state)
    if report.ended_by in MACHINE_REASONS:
        return outcome_for_machine(report.ended_by)
    if not report.transcript.said("callee"):
        return CallOutcome(disposition="failed", notes="Answered, but nothing was heard.")
    try:
        result = await ctx.llm.chat(
            ctx.config.model_profile,
            classification_messages(ctx.config, report.transcript),
            response_model=CallOutcome,
            temperature=0,
        )
        model = result.parsed
    except PlatformError as exc:
        await ctx.events.log("warning", "Could not summarize a call", code=exc.code)
        model = CallOutcome(disposition="completed", notes="The call summary is unavailable.")
    if not isinstance(model, CallOutcome):
        model = CallOutcome(disposition="completed")
    outcome = reconcile(model, report.transcript)
    if report.error_code:
        return outcome.model_copy(
            update={
                "disposition": "failed",
                "notes": f"The call ended early ({report.error_code}).",
            }
        )
    return outcome


async def _write_row(
    ctx: RunContext[VoiceCallerConfig],
    contact: ContactRow,
    call_id: str,
    outcome: CallOutcome,
    duration: int | None,
    answered: bool,
) -> str:
    config = ctx.config
    values: list[Cell] = list(row_values(contact, call_id, outcome, duration, _now()))
    try:
        await ctx.google.sheets.update_values(
            config.spreadsheet_id, result_range(config, contact.row), [values]
        )
        status = STATUS_AFTER.get(outcome.disposition)
        if status is None and answered:
            status = "called"  # someone picked up: never dial them again automatically
        if status is not None:
            await ctx.google.sheets.update_values(
                config.spreadsheet_id, status_range(config, contact.row), [[status]]
            )
    except PlatformError as exc:
        await ctx.events.log("warning", "Sheet write failed", row=contact.row, code=exc.code)
        return "failed"
    return "written"


async def _call_one(
    ctx: RunContext[VoiceCallerConfig], runner: CallRunner, contact: ContactRow
) -> tuple[CallOutcome, str, CallReport | None]:
    config = ctx.config
    dialed_now = False

    async def dial() -> VoiceCall:
        nonlocal dialed_now
        dialed_now = True
        return await ctx.voice.dial(
            contact.phone,
            idempotency_key=f"voice:{ctx.run.id}:{contact.row}",
            ring_timeout_seconds=config.ring_timeout_seconds,
            max_duration_seconds=config.max_call_seconds + 60,
        )

    try:
        call = await ctx.idempotency.once(f"voice-call:{contact.row}", dial, result_type=VoiceCall)
    except OutcomeUnknown:
        note = "The run was interrupted while dialing; the call may have happened."
        return CallOutcome(disposition="failed", notes=note), "", None
    except PlatformError as exc:
        return CallOutcome(disposition="failed", notes=f"Dialing failed ({exc.code})."), "", None
    if not dialed_now:
        # A retried run found the call an earlier attempt placed: never redial or rejoin it.
        current = await ctx.voice.hangup(call.id)
        note = "An earlier attempt placed this call; its conversation outcome is unknown."
        report = CallReport(
            call.id, current.answered_at is not None, "earlier_attempt", current.state
        )
        return CallOutcome(disposition="failed", notes=note), call.id, report
    report = await runner.run_call(ctx, call, contact, config)
    return await _outcome(ctx, report), call.id, report


async def run_campaign(ctx: RunContext[VoiceCallerConfig], runner: CallRunner) -> dict[str, Any]:
    config = ctx.config
    values = await ctx.google.sheets.get_values(config.spreadsheet_id, config.input_range)
    plan = classify(read_rows(values, config.input_start_row), config.max_calls)
    summary: Counter[str] = Counter(planned=len(plan.eligible), skipped=len(plan.skipped))
    rows: list[dict[str, Any]] = []
    if not plan.eligible:
        await ctx.events.log("info", "No consenting, ready contacts to call")
        return _result("nothing_to_call", summary, rows)
    answer = await ctx.input.ask(
        **build_request(plan, config),
        timeout_seconds=max(60, min(86_400, int(ctx.input.remaining_wait_seconds))),
    )
    if answer.value != "approve":
        return _result("cancelled", summary, rows)
    await ctx.google.sheets.update_values(
        config.spreadsheet_id, f"{config.result_tab}!A1:J1", [list[Cell](HEADER)]
    )
    await runner.prepare(ctx, config)
    for index, contact in enumerate(plan.eligible):
        await ctx.events.progress(
            round(100 * index / len(plan.eligible)),
            f"Calling contact {index + 1} of {len(plan.eligible)}",
            step="calls",
        )
        outcome, call_id, report = await _call_one(ctx, runner, contact)
        answered = bool(report and report.answered)
        duration = report.duration_seconds if report else None
        sheet = await _write_row(ctx, contact, call_id, outcome, duration, answered)
        summary["called"] += 1
        summary["answered"] += int(answered)
        summary[outcome.disposition] += 1
        if report and report.latency_p50_ms is not None:
            await ctx.events.metric("turn_latency_p50_ms", round(report.latency_p50_ms), unit="ms")
        rows.append(
            {
                "row": contact.row,
                "name": contact.name,
                "phoneMasked": row_values(contact, call_id, outcome, None, "")[2],
                "callId": call_id or None,
                "disposition": outcome.disposition,
                "interest": outcome.interest,
                "callbackRequested": outcome.callback_requested,
                "followUp": outcome.follow_up,
                "notes": outcome.notes,
                "durationSeconds": duration,
                "sheetWrite": sheet,
            }
        )
    await ctx.events.progress(100, "All calls done", step="done")
    return _result("approved", summary, rows)


def _result(decision: str, summary: Counter[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"planned": summary["planned"], "skipped": summary["skipped"]}
    counts |= {"called": summary["called"], "answered": summary["answered"]}
    counts |= {_camel(d): summary[d] for d in DISPOSITIONS}
    return {"operatorDecision": decision, "summary": counts, "rows": rows}


def build_agent(runner: CallRunner | None = None) -> Agent:
    agent = Agent("voice-call-center", config_model=VoiceCallerConfig)

    @agent.run
    async def run(ctx: RunContext[VoiceCallerConfig]) -> dict[str, Any]:
        return await run_campaign(ctx, runner or LiveKitCallRunner())

    return agent


agent = build_agent()
