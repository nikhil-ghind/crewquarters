"""The two model calls: query expansion and the brief itself, each with one repair retry."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from crewquarters.errors import InvalidInput
from crewquarters.llm import ChatResult
from personal_space.models import Expansion, SpaceBrief
from personal_space.prompts import (
    EXPAND_SYSTEM,
    REPAIR,
    SYNTH_SYSTEM,
    expand_prompt,
    synth_prompt,
)


async def _structured(
    ctx: Any, system: str, user: str, model: type[BaseModel], *, profile: str, key: str
) -> ChatResult | None:
    """One structured call; a reply that does not match the schema gets a single repair turn."""
    prompt: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for attempt in (1, 2):
        try:
            chat: ChatResult = await ctx.llm.chat(
                profile,
                prompt,
                temperature=0,
                response_model=model,
                idempotency_key=f"{key}-a{attempt}-v1",
            )
            return chat
        except InvalidInput as exc:
            if exc.code != "STRUCTURED_OUTPUT_INVALID":
                raise
            await ctx.events.log(
                "warning", f"{key} output did not match the schema (attempt {attempt})"
            )
            if attempt == 1:
                prompt = [
                    *prompt,
                    {"role": "assistant", "content": str(exc.details.get("raw", ""))},
                    {"role": "user", "content": REPAIR},
                ]
    return None


async def expand(
    ctx: Any,
    shown: dict[str, tuple[str, str]],
    *,
    boundary: str,
    used: list[str],
    intent: str | None,
    limit: int,
    profile: str,
) -> tuple[list[str], ChatResult | None]:
    """Model-proposed follow-up queries. An unusable reply just means none."""
    user = expand_prompt(shown, boundary, used, intent, limit)
    chat = await _structured(
        ctx, EXPAND_SYSTEM, user, Expansion, profile=profile, key="space-expand"
    )
    if chat is None or not isinstance(chat.parsed, Expansion):
        return [], chat
    return list(chat.parsed.queries), chat


async def synthesize(
    ctx: Any,
    shown: dict[str, tuple[str, str]],
    *,
    boundary: str,
    intent: str | None,
    today: date,
    profile: str,
) -> tuple[SpaceBrief | None, ChatResult | None]:
    user = synth_prompt(shown, boundary, intent, today)
    chat = await _structured(
        ctx, SYNTH_SYSTEM, user, SpaceBrief, profile=profile, key="space-brief"
    )
    if chat is None or not isinstance(chat.parsed, SpaceBrief):
        return None, chat
    return chat.parsed, chat
