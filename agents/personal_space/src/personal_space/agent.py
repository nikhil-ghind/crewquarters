"""personal-space: a cited, personalized brief built from the owner's own knowledge base."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from crewquarters import Agent, RunContext
from crewquarters.errors import InputTimeout
from crewquarters.llm import ChatResult
from crewquarters.untrusted import new_boundary
from personal_space.config import PersonalSpaceConfig
from personal_space.discovery import (
    Retrieved,
    clean_query,
    derive_queries,
    probe_queries,
    prompt_refs,
    run_queries,
    select_for_prompt,
)
from personal_space.models import ModelInfo, SpaceResult, Stats
from personal_space.synthesize import expand, synthesize
from personal_space.validate import (
    cited_ids,
    fallback_themes,
    sources_for,
    validate_brief,
)

agent = Agent("personal-space", config_model=PersonalSpaceConfig, result_model=SpaceResult)
INTENT_KEY = "space-intent-v1"
EXPAND_SAMPLE_CHARS = 4000


async def _resolve_intent(ctx: RunContext[PersonalSpaceConfig]) -> str | None:
    """The configured intent, else a Crew Request. No answer in time means no intent."""
    config = ctx.config
    if config.intent:
        return config.intent
    if not config.ask_for_intent:
        return None
    timeout = min(config.intent_wait_seconds, int(ctx.input.remaining_wait_seconds))
    if timeout < 1:
        return None
    try:
        answer = await ctx.input.ask(
            INTENT_KEY,
            "What do you want from your space?",
            "Say what you are working on or looking for, and the brief will be built around it. "
            "Leave it empty to let your knowledge base speak for itself.",
            schema={
                "type": "object",
                "properties": {"intent": {"type": "string", "maxLength": 500}},
            },
            timeout_seconds=timeout,
        )
    except InputTimeout:
        await ctx.events.log("warning", "no answer to the intent question; continuing without one")
        return None
    data = answer.data if isinstance(answer.data, dict) else {}
    return str(data.get("intent") or "").strip() or None


def _model_info(profile: str, chat: ChatResult | None) -> ModelInfo:
    if chat is None:
        return ModelInfo(profile=profile)
    return ModelInfo(
        profile=profile, provider=chat.provider, model=chat.model, locality=chat.locality
    )


@agent.run
async def run(ctx: RunContext[PersonalSpaceConfig]) -> SpaceResult:
    config = ctx.config
    tz = ZoneInfo(config.timezone)
    scheduled = ctx.run.trigger == "schedule" and ctx.run.scheduled_for is not None
    reference = ctx.run.scheduled_for if scheduled and ctx.run.scheduled_for else datetime.now(UTC)
    today = reference.astimezone(tz).date()

    await ctx.events.progress(5, "Asking what you want from your space", step="intent")
    intent = await _resolve_intent(ctx)

    retrieved = Retrieved()
    await ctx.events.progress(10, "Exploring your knowledge base", step="discover")
    await run_queries(ctx, config, probe_queries(intent), retrieved)

    boundary = new_boundary()
    chat: ChatResult | None = None
    room = config.max_queries - len(retrieved.queries)
    if retrieved.passages and room > 0:
        await ctx.events.progress(35, "Following up on what turned up", step="expand")
        sample = select_for_prompt(retrieved.passages.values(), EXPAND_SAMPLE_CHARS)
        shown, _ = prompt_refs(sample)
        proposed, chat = await expand(
            ctx,
            shown,
            boundary=boundary,
            used=retrieved.queries,
            intent=intent,
            limit=room,
            profile=config.model_profile,
        )
        follow_ups = [q for q in (clean_query(p) for p in proposed) if q][:room]
        follow_ups += derive_queries(
            retrieved.passages.values(), [*retrieved.queries, *follow_ups], room - len(follow_ups)
        )
        await run_queries(ctx, config, follow_ups, retrieved)

    stats = Stats(
        queries_run=len(retrieved.queries),
        passages_considered=len(retrieved.seen),
        passages_used=len(retrieved.passages),
        documents_seen=len(retrieved.documents),
    )
    await ctx.events.metric("passages_used", stats.passages_used, unit="passages")
    generated_at = datetime.now(UTC)

    if stats.passages_used < config.min_evidence_passages:
        additions = [
            "Add notes about your goals, current projects, and priorities.",
            "Add more documents about the topics you care about, then run this again.",
        ]
        if intent:
            additions.insert(0, f"Add documents that cover: {intent}")
        top = sorted(retrieved.passages.values(), key=lambda p: p.score, reverse=True)[:5]
        await ctx.events.progress(100, "Not enough context to personalize yet", step="done")
        return SpaceResult(
            status="insufficient_context",
            domain=None,
            intent=intent,
            summary=(
                f"Only {stats.passages_used} relevant passage(s) were found, so there is not "
                "enough in this knowledge base to build a personalized brief yet."
            ),
            generated_at=generated_at,
            degraded=False,
            suggested_additions=additions,
            sources=sources_for([p.citation_id for p in top], retrieved.passages),
            stats=stats,
            model=_model_info(config.model_profile, chat),
        )

    await ctx.events.progress(55, "Writing your brief", step="synthesize")
    selected = select_for_prompt(retrieved.passages.values(), config.max_prompt_chars)
    shown, refs = prompt_refs(selected)
    brief, synth_chat = await synthesize(
        ctx,
        shown,
        boundary=boundary,
        intent=intent,
        today=today,
        profile=config.model_profile,
    )
    chat = synth_chat or chat
    themes, highlights, explore = validate_brief(brief, refs) if brief else ([], [], [])

    if not themes and not highlights:
        # The model gave nothing usable (or nothing it could cite): show the evidence itself.
        await ctx.events.log(
            "warning", "no usable brief from the model; showing passages by document"
        )
        themes = fallback_themes(retrieved.passages.values())
        brief = None
    degraded = brief is None
    await ctx.events.artifact(
        "space-brief",
        "application/json",
        summary=(
            f"{len(themes)} themes, {len(highlights)} highlights "
            f"from {stats.documents_seen} documents"
        ),
    )
    await ctx.events.progress(100, "Your brief is ready", step="done")
    summary = (
        brief.summary.strip()
        if brief
        else (
            f"The model could not write a brief this time, so these are the most relevant "
            f"passages from {stats.documents_seen} document(s), grouped by document."
        )
    )
    return SpaceResult(
        status="ready",
        domain=(brief.domain.strip() or None) if brief else None,
        intent=intent,
        summary=summary,
        generated_at=generated_at,
        degraded=degraded,
        themes=themes,
        highlights=highlights,
        explore_next=explore,
        sources=sources_for(cited_ids(themes, highlights), retrieved.passages),
        stats=stats,
        model=_model_info(config.model_profile, chat),
    )
