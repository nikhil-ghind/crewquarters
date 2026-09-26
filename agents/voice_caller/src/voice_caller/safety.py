"""Safety guardrails for the voice agents.

Two failure modes this module addresses:

1. Background audio (other people talking, TV, etc.) gets transcribed by
   the STT and pollutes the chat context. Azure OpenAI's content filter
   then refuses to generate ANY response, killing the call silently.

2. The agent's own response can occasionally trip a filter (e.g. it echoes
   a word the expert said). Without guardrails this loops forever in retry.

The mitigation:
  - sanitize_user_text() masks the handful of tokens that reliably fire
    Azure's `sexual` / `hate` filters, in the text the LLM sees. It must
    run BEFORE the LLM is called — from the agent's on_user_turn_completed
    hook, not from conversation_item_added, which fires after the item is
    already in the LLM's context and so protects nothing. It must NOT be
    applied to the stored transcript: the researcher's record is verbatim.
  - is_content_filter_error() identifies Azure's content_filter response
    structure so the agent can recover instead of retrying in a tight loop.
  - graceful_recovery_message() returns something the agent can say when a
    filter triggers. It moves the conversation ON rather than asking the
    person to repeat the thing that tripped the filter.
"""
# Ported from truestar-voice (TrueStar) at 4c34cce: src/safety.py.
# Changes for Crewquarters are marked "Crewquarters:".

from __future__ import annotations

import re
from collections.abc import Iterator

# Kept short and surgical: these are the tokens that make Azure's `sexual`
# / `hate` filters fire. Harmless words that never trip a filter ("damn",
# "crap", "jerk") are deliberately absent, and so is "dick", which is a
# common first name — masking a person's name in what the LLM sees was a
# real false positive in the previous list.
_PROFANITY_PATTERNS = [
    # Word-internal match is intentional for compounds like "motherf*cker".
    r"f+u+c+k+(?:ing|ed|er|ers)?",
    # Anchored so "shiitake" and "mishit" survive; "bullshit" still masks.
    r"\b(?:bull)?s+h+i+t+(?:ty|head|s)?\b",
    r"\ba+s+s+(?:hole|holes|h+a+t+)\b",
    r"\bb+i+t+c+h+(?:es|ing|y)?\b",
    r"\bc+u+n+t+s?\b",
    r"\bp+u+s+s+y+\b",
    r"\bb+a+s+t+a+r+d+s?\b",
    # Slurs. Anchored so "snigger" (British English) is not masked.
    r"\bn+i+g+g+(?:a|er|as|ers)\b",
    r"\bf+a+g+(?:got|gots|s)?\b",
    r"\br+e+t+a+r+d+(?:s|ed)?\b",
]

_PROFANITY_RE = re.compile("|".join(_PROFANITY_PATTERNS), re.IGNORECASE)


def sanitize_user_text(text: str) -> tuple[str, bool]:
    """Mask profanity in a transcribed turn before it enters the LLM context.

    Returns (cleaned_text, was_modified). Use the cleaned text for the LLM
    only; persist the original.
    """
    if not text:
        return text, False

    def _mask(m: re.Match[str]) -> str:
        return "*" * len(m.group(0))

    cleaned, n = _PROFANITY_RE.subn(_mask, text)
    return cleaned, n > 0


_MAX_CHAIN_DEPTH = 10


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Walk __cause__/__context__ with a depth bound and a cycle guard.

    Defensive: livekit emits TTSError (a pydantic model) where __cause__ is
    blocked by pydantic.BaseModel.__getattr__. Any access error is swallowed
    rather than letting the error handler itself crash and mask the
    original problem.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < _MAX_CHAIN_DEPTH and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        depth += 1
        try:
            cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        except Exception:
            return


def is_content_filter_error(exc: BaseException) -> bool:
    """Detect Azure OpenAI's content_filter rejection from an exception chain."""
    try:
        for e in _exception_chain(exc):
            msg = str(e).lower()
            if "content_filter" in msg or "responsibleaipolicyviolation" in msg:
                return True
    except Exception:
        return False
    return False


def is_tts_billing_error(exc: BaseException) -> bool:
    """Detect TTS provider billing/auth failures (402, 401, 403).

    These are NOT recoverable mid-call: no retry will succeed once the
    provider's wallet is empty or the key is rejected. Used to short-circuit
    the agent's normal retry loop and end the call cleanly instead of
    leaving the far end on a dead room with no audio.
    """
    billing_markers = (
        "status_code=402",
        "status_code=401",
        "status_code=403",
        "payment required",
        "insufficient",
        "quota exceeded",
    )
    try:
        for e in _exception_chain(exc):
            msg = str(e).lower()
            if any(m in msg for m in billing_markers):
                return True
    except Exception:
        return False
    return False


# Spoken when a turn was filtered. These move ON rather than asking the
# person to repeat themselves: "could you say that again?" invites them to
# repeat exactly the text that tripped the filter, which fails again and
# loops — the same apology on every turn is one of the most robotic things
# a caller can hear. The agent must also drop the offending user turn from
# the chat context before the next generation; see campaign_agent.py.
_RECOVERY_PHRASES = [
    "Sorry, the line dropped for a second there. Let's keep going.",
    "I lost you for a moment. No problem, let's move on.",
    "Line's a bit rough. Let's pick up from the next point.",
]


def graceful_recovery_message(attempt: int = 0) -> str:
    return _RECOVERY_PHRASES[attempt % len(_RECOVERY_PHRASES)]
