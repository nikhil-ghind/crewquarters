from __future__ import annotations

from voice_helpers import make_config

from caller_agent.rows import ContactRow, Plan, Skipped
from voice_caller.approval import approval_key, build_request

PLAN = Plan(
    eligible=[
        ContactRow(2, "Asha Rao", "+14155550101", "yes", ""),
        ContactRow(3, "Ben", "+14155550103", "yes", ""),
    ],
    skipped=[Skipped(4, "Cy", "consent")],
)


def test_key_changes_when_the_brief_or_recipients_change() -> None:
    base = approval_key(PLAN, make_config())
    assert base.startswith("confirm-voice-calls-v1:") and len(base) <= 128
    assert approval_key(PLAN, make_config(purpose="something else")) != base
    assert approval_key(PLAN, make_config(maxCallSeconds=300)) != base
    fewer = Plan(eligible=PLAN.eligible[:1], skipped=[])
    assert approval_key(fewer, make_config()) != base
    assert approval_key(PLAN, make_config()) == base


def test_request_masks_numbers_and_states_the_consequence() -> None:
    request = build_request(PLAN, make_config())
    assert request["title"] == "Approve 2 AI phone conversations"
    text = str(request["preview"])
    assert "••••0101" in text and "+14155550101" not in text
    assert "automated AI assistant" in text  # the example greeting
    assert "2 automated AI phone conversations of up to 4 minutes each" in request["consequence"]
    assert [c.value for c in request["choices"]] == ["approve", "cancel"]
