"""The call transcript as heard (callee) and as spoken (agent), without delivery markup.

Crewquarters: replaces truestar-voice's TranscriptBuffer; turns are kept in memory for the outcome
summary and are never logged or written out in full.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from voice_caller.texture import strip_markup

Speaker = Literal["agent", "callee"]
LABELS = {"agent": "Agent", "callee": "Callee"}


@dataclass(frozen=True)
class Turn:
    speaker: Speaker
    text: str
    at_ms: int


class Transcript:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self.turns: list[Turn] = []

    def add(self, speaker: Speaker, text: str) -> Turn | None:
        cleaned = strip_markup(text).strip()
        if not cleaned:
            return None
        turn = Turn(speaker, cleaned, int((self._clock() - self._start) * 1000))
        self.turns.append(turn)
        return turn

    def said(self, speaker: Speaker) -> list[str]:
        return [t.text for t in self.turns if t.speaker == speaker]

    def as_text(self, max_chars: int = 8000) -> str:
        """``Agent: …`` / ``Callee: …`` lines; the most recent part if it is too long."""
        text = "\n".join(f"{LABELS[t.speaker]}: {t.text}" for t in self.turns)
        return text if len(text) <= max_chars else "…" + text[-max_chars:]
