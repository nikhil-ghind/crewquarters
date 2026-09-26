"""Simulated callees, from a scenario's ``voice.callees`` section.

```yaml
voice:
  callees:
    "+15555550101": {outcome: answer, ringSeconds: 1.5, script: ["Hello?", "Sure, go ahead."]}
    "+15555550102": {outcome: voicemail}
    "+15555550103": {outcome: busy}
```

``answer`` speaks the first line when it picks up and each later line after the agent finishes a
turn. ``voicemail`` plays a long greeting. ``busy``, ``no-answer``, and ``failed`` never pick up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, cast

Outcome = Literal["answer", "voicemail", "busy", "no-answer", "failed"]
OUTCOMES: frozenset[str] = frozenset({"answer", "voicemail", "busy", "no-answer", "failed"})
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
VOICEMAIL_GREETING = (
    "Hi, you've reached the voicemail of Jordan Lee. I can't take your call right now. "
    "Please leave your name, your number, and a short message after the tone, "
    "and I'll get back to you as soon as I can. Thanks, and have a great day."
)


@dataclass(frozen=True)
class CalleeScenario:
    outcome: Outcome = "answer"
    script: tuple[str, ...] = ()
    ring_seconds: float = 1.0
    voice: str = "am_adam"
    # Leave the call after the last line (otherwise wait for the agent to hang up).
    hang_up_after_script: bool = False


UNKNOWN_NUMBER = CalleeScenario(outcome="no-answer", ring_seconds=0.5)


def parse_callees(raw: dict[str, Any] | None) -> dict[str, CalleeScenario]:
    callees: dict[str, CalleeScenario] = {}
    for number, spec in (raw or {}).items():
        if not _E164.match(str(number)):
            raise ValueError(f"voice.callees: {number!r} is not an E.164 number")
        spec = spec or {}
        outcome = str(spec.get("outcome", "answer"))
        if outcome not in OUTCOMES:
            raise ValueError(f"voice.callees[{number}].outcome must be one of {sorted(OUTCOMES)}")
        callees[str(number)] = CalleeScenario(
            outcome=cast(Outcome, outcome),
            script=tuple(str(line) for line in spec.get("script", [])),
            ring_seconds=float(spec.get("ringSeconds", 1.0)),
            voice=str(spec.get("voice", "am_adam")),
            hang_up_after_script=bool(spec.get("hangUpAfterScript", False)),
        )
    return callees
