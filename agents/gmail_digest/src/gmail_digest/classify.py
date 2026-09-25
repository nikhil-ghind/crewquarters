"""Map step: classify one batch of messages with structured LLM output."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from crewquarters.errors import InvalidInput
from crewquarters.llm import ChatResult
from gmail_digest.models import Classification, FetchedMessage
from gmail_digest.prompts import REPAIR, SYSTEM, batch_prompt


class BatchItem(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    ref: str
    priority: Literal["urgent", "important", "low"]
    reason: str = Field(max_length=500)
    next_action: str = Field(max_length=300)
    uncertain: bool = False


class DigestBatch(BaseModel):
    items: list[BatchItem]


NOT_CLASSIFIED = Classification(
    "important", "Not classified automatically", "Review this message", True
)
BATCH_FAILED = Classification(
    "important", "Could not be classified automatically", "Review this message", True
)


async def classify_batch(
    ctx: Any,
    messages: list[FetchedMessage],
    index: int,
    *,
    boundary: str,
    profile: str,
    tz_name: str,
) -> tuple[dict[str, Classification], ChatResult | None]:
    """Classify ``messages``; every message gets a classification keyed by its Gmail id."""
    refs = {f"m{position + 1}": message for position, message in enumerate(messages)}
    prompt: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": batch_prompt(refs, boundary, tz_name)},
    ]
    chat: ChatResult | None = None
    for attempt in (1, 2):
        try:
            chat = await ctx.llm.chat(
                profile,
                prompt,
                temperature=0,
                response_model=DigestBatch,
                idempotency_key=f"digest-batch-{index}-a{attempt}-v1",
            )
            break
        except InvalidInput as exc:
            if exc.code != "STRUCTURED_OUTPUT_INVALID":
                raise
            await ctx.events.log(
                "warning", f"batch {index} output did not match the schema (attempt {attempt})"
            )
            if attempt == 1:
                prompt = [
                    *prompt,
                    {"role": "assistant", "content": str(exc.details.get("raw", ""))},
                ]
                prompt.append({"role": "user", "content": REPAIR})
    if chat is None:
        return {message.id: BATCH_FAILED for message in messages}, None

    batch: DigestBatch = chat.parsed
    classified: dict[str, Classification] = {}
    for item in batch.items:
        message = refs.get(item.ref)
        if message is None:
            await ctx.events.log("warning", f"batch {index}: dropped unknown ref {item.ref}")
            continue
        if message.id not in classified:
            classified[message.id] = Classification(
                item.priority, item.reason, item.next_action, item.uncertain
            )
    for message in messages:
        classified.setdefault(message.id, NOT_CLASSIFIED)
    return classified, chat
