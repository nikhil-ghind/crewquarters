"""The owner's approval card: masked recipients, the brief, the greeting, and the exact count."""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from caller_agent.rows import Plan
from crewquarters import Choice, key_value_block, table_block, text_block
from crewquarters.redact import mask_phone
from voice_caller.config import VoiceCallerConfig
from voice_caller.prompts import build_greeting


def approval_key(plan: Plan, config: VoiceCallerConfig) -> str:
    """Stable per (brief, disclosure, limits, recipients): any change needs a new approval."""
    payload = json.dumps(
        {
            "agent": config.agent_name,
            "organization": config.organization,
            "purpose": config.purpose,
            "talkingPoints": config.talking_points,
            "questions": config.questions,
            "disclosure": config.disclosure,
            "maxCallSeconds": config.max_call_seconds,
            "recipients": sorted([c.row, c.phone] for c in plan.eligible),
        },
        sort_keys=True,
    )
    return "confirm-voice-calls-v1:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def build_request(plan: Plan, config: VoiceCallerConfig) -> dict[str, Any]:
    """Keyword arguments for ``ctx.input.ask`` (everything except ``timeout_seconds``)."""
    count = len(plan.eligible)
    minutes = config.max_call_seconds // 60
    brief = [f"Purpose: {config.purpose}"]
    brief += [f"Talking point: {p}" for p in config.talking_points]
    brief += [f"Question: {q}" for q in config.questions]
    first = plan.eligible[0].name if plan.eligible else ""
    # A fixed seed: the card shows one representative greeting (not a security use).
    example = build_greeting(config, first, random.Random(0))  # noqa: S311
    preview = [
        table_block(
            ["Row", "Name", "Number", "Consent"],
            [[c.row, c.name, mask_phone(c.phone), "validated"] for c in plan.eligible],
        ),
        text_block("\n".join(brief)),
        text_block(f"Each call opens like this: “{example}”"),
    ]
    if plan.skipped:
        preview.append(
            table_block(
                ["Row", "Name", "Reason"], [[s.row, s.name, s.reason] for s in plan.skipped]
            )
        )
    preview.append(
        key_value_block(
            [
                ("Recipients", str(count)),
                ("Call cap", str(config.max_calls)),
                ("Longest call", f"{minutes} min"),
                ("Skipped rows", str(len(plan.skipped))),
            ]
        )
    )
    return {
        "key": approval_key(plan, config),
        "title": f"Approve {_plural(count, 'AI phone conversation')}",
        "prompt": (
            f"The voice agent is ready to call {_plural(count, 'consenting contact')} from your "
            "sheet and hold a short conversation about your brief. Review the recipients and the "
            "brief before approving."
        ),
        "choices": [
            Choice("approve", f"Approve {_plural(count, 'call')}", "primary"),
            Choice("cancel", "Cancel run", "secondary"),
        ],
        "preview": preview,
        "consequence": (
            f"{_plural(count, 'automated AI phone conversation')} of up to {minutes} minutes each "
            f"will start now. Every call opens by saying it is {config.disclosure} calling for "
            f"{config.organization}."
        ),
    }
