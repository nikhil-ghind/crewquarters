"""The operator approval request: masked recipients, script, skips, and the exact call count."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from caller_agent.config import CallerConfig
from caller_agent.rows import Plan
from crewquarters import Choice, key_value_block, table_block, text_block
from crewquarters.redact import mask_phone


def approval_key(plan: Plan, script: str, disclosure: str) -> str:
    """Stable per (script, disclosure, recipients): a changed sheet or script needs a fresh approval."""
    payload = json.dumps(
        {
            "script": script,
            "disclosure": disclosure,
            "recipients": sorted([c.row, c.phone] for c in plan.eligible),
        },
        sort_keys=True,
    )
    return "confirm-calls-v1:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def personalised(script: str, name: str) -> str:
    return script.replace("{name}", name)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def build_request(plan: Plan, config: CallerConfig) -> dict[str, Any]:
    """Keyword arguments for ``ctx.input.ask`` (everything except ``timeout_seconds``)."""
    count = len(plan.eligible)
    preview = [
        table_block(
            ["Row", "Name", "Number", "Consent"],
            [[c.row, c.name, mask_phone(c.phone), "validated"] for c in plan.eligible],
        ),
        text_block(f"Disclosure: {config.disclosure}\nScript: {config.script}"),
    ]
    if plan.skipped:
        preview.append(
            table_block(["Row", "Name", "Reason"], [[s.row, s.name, s.reason] for s in plan.skipped])
        )
    preview.append(
        key_value_block(
            [
                ("Recipients", str(count)),
                ("Call cap", str(config.max_calls)),
                ("Skipped rows", str(len(plan.skipped))),
            ]
        )
    )
    return {
        "key": approval_key(plan, config.script, config.disclosure),
        "title": f"Approve {_plural(count, 'automated call')}",
        "prompt": (
            f"The caller agent is ready to call {_plural(count, 'consenting recipient')} from your sheet. "
            "Review the recipients, disclosure, and script before approving."
        ),
        "choices": [
            Choice("approve", f"Approve {_plural(count, 'call')}", "primary"),
            Choice("cancel", "Cancel run", "secondary"),
        ],
        "preview": preview,
        "consequence": (
            f"{_plural(count, 'automated call')} will be placed now to the numbers above. "
            "Each call starts with the disclosure."
        ),
    }
