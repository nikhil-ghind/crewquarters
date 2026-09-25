"""daily-gmail-digest: previous local calendar day's Gmail, grouped by urgency."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from crewquarters import Agent, RunContext
from crewquarters.errors import AgentError, NeedsConnection, PlatformError
from crewquarters.google.mime import strip_quoted_replies
from crewquarters.untrusted import new_boundary
from gmail_digest.classify import classify_batch
from gmail_digest.config import DigestConfig
from gmail_digest.models import Classification, DigestResult, FetchedMessage
from gmail_digest.query import build_query
from gmail_digest.reduce import build_digest
from gmail_digest.window import day_window, target_date

agent = Agent("daily-gmail-digest", config_model=DigestConfig, result_model=DigestResult)
FETCH_CONCURRENCY = 5


async def _fetch_all(ctx: RunContext[DigestConfig], ids: list[str]) -> list[FetchedMessage]:
    limit = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def fetch(message_id: str) -> FetchedMessage | None:
        async with limit:
            try:
                message = await ctx.google.gmail.get_message(
                    message_id, max_chars=ctx.config.max_chars_per_message
                )
            except PlatformError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                # Deleted or moved between list and get: skip it rather than fail the whole digest.
                await ctx.events.log(
                    "warning", f"message {message_id} is no longer available; skipped"
                )
                return None
        return FetchedMessage(
            id=message.id or message_id,
            thread_id=message.thread_id,
            sender=message.sender,
            subject=message.subject or "(no subject)",
            received_at=message.internal_date,
            text=strip_quoted_replies(message.text_body) or message.snippet,
            web_link=message.web_link,
        )

    fetched = await asyncio.gather(*(fetch(message_id) for message_id in ids))
    return [message for message in fetched if message is not None]


@agent.run
async def run(ctx: RunContext[DigestConfig]) -> DigestResult:
    config = ctx.config
    tz = ZoneInfo(config.timezone)
    scheduled = ctx.run.trigger == "schedule" and ctx.run.scheduled_for is not None
    reference = ctx.run.scheduled_for if scheduled and ctx.run.scheduled_for else datetime.now(UTC)
    day = target_date(reference, tz, config.target_date)
    window = day_window(day, tz)
    query = build_query(
        window[0], window[1], list(config.exclude_categories), config.include_labels
    )
    await ctx.events.progress(
        5, f"Listing Gmail messages for {day.isoformat()} ({config.timezone})", step="list"
    )

    try:
        iteration = ctx.google.gmail.iter_message_ids(query, limit=config.max_messages)
        ids = [message_id async for message_id, _ in iteration]
        await ctx.events.progress(15, f"Fetching {len(ids)} messages", step="fetch")
        messages = await _fetch_all(ctx, ids)
    except NeedsConnection as exc:
        raise AgentError(
            "Google access has expired or is missing; reconnect Google in Connections",
            code="GOOGLE_RECONNECT_REQUIRED",
        ) from exc
    if iteration.truncated:
        await ctx.events.log(
            "warning", f"stopped at maxMessages={config.max_messages}; the digest may be incomplete"
        )

    classifications: dict[str, Classification] = {}
    model: dict[str, str | None] = {"profile": config.model_profile}
    boundary = new_boundary()
    batches = [
        messages[i : i + config.batch_size] for i in range(0, len(messages), config.batch_size)
    ]
    for index, batch in enumerate(batches):
        verdicts, chat = await classify_batch(
            ctx,
            batch,
            index,
            boundary=boundary,
            profile=config.model_profile,
            tz_name=config.timezone,
        )
        classifications.update(verdicts)
        if chat is not None:
            model = {
                "profile": config.model_profile,
                "provider": chat.provider,
                "model": chat.model,
                "locality": chat.locality,
            }
        await ctx.events.progress(
            20 + 75 * (index + 1) / len(batches),
            f"Classified batch {index + 1}/{len(batches)}",
            step="classify",
        )

    digest = build_digest(
        day=day,
        tz=tz,
        window=window,
        messages=messages,
        classifications=classifications,
        truncated=iteration.truncated,
        model=model,
    )
    await ctx.events.metric("messages_processed", len(messages), unit="messages")
    await ctx.events.artifact(
        "digest",
        "application/json",
        summary=(
            f"{digest.counts.urgent} urgent, {digest.counts.important} important, "
            f"{digest.counts.low_priority} low"
        ),
    )
    await ctx.events.progress(100, "Digest ready", step="done")
    return digest
