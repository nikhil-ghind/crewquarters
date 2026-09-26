"""A deterministic speech engine for development and tests (no models).

Transcription returns the lines queued with ``queue_transcripts`` in order, so a simulated
callee can register what it is about to say and the agent "hears" exactly that. Lines are queued
per channel (the fake platform uses one channel per call), so a line one call never consumed is
never heard on the next. A line may carry the time it finishes playing: it is heard only from
then on, and when several are ready the latest wins, so a line the listener's voice-activity
detector never segmented is skipped instead of being replayed one turn late. Synthesis repeats
a short bundled speech clip (real speech, so voice-activity detectors treat it as speech), about
0.3 s per word, and records the text it was asked to say.
"""

from __future__ import annotations

import threading
import time
import wave
from collections import deque
from collections.abc import Callable, Iterator
from importlib import resources

import numpy as np

from crewquarters_speech.audio import OUTPUT_SAMPLE_RATE, Samples, split_clauses

SECONDS_PER_WORD = 0.3
READY_SLACK_S = 0.3  # a transcription request may arrive just before playout has finished
FAKE_VOICES = ["af_sarah", "am_adam"]


def _clip() -> bytes:
    asset = resources.files("crewquarters_speech").joinpath("assets/fake_voice.wav")
    with asset.open("rb") as f, wave.open(f, "rb") as w:
        if w.getframerate() != OUTPUT_SAMPLE_RATE or w.getsampwidth() != 2:
            raise RuntimeError("fake_voice.wav must be 16-bit PCM at 24 kHz")
        return w.readframes(w.getnframes())


class FakeSpeechEngine:
    name = "fake"

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        # Per channel: (text, time the line finishes playing, or None for "heard now, in order").
        self._transcripts: dict[str, deque[tuple[str, float | None]]] = {}
        self.synthesized: list[str] = []
        self.transcribed: list[str] = []
        self._voice = _clip()

    def queue_transcripts(
        self, lines: list[str], channel: str = "", available_at: float | None = None
    ) -> None:
        with self._lock:
            queue = self._transcripts.setdefault(channel, deque())
            queue.extend((line, available_at) for line in lines)

    def transcribe(self, samples: Samples, sample_rate: int, channel: str = "") -> str:
        with self._lock:
            text = self._next(self._transcripts.get(channel))
            self.transcribed.append(text)
            return text

    def _next(self, queue: deque[tuple[str, float | None]] | None) -> str:
        if not queue:
            return ""
        if queue[0][1] is None:
            return queue.popleft()[0]
        now = self._clock()
        ready = [
            entry for entry in queue if entry[1] is not None and entry[1] <= now + READY_SLACK_S
        ]
        if not ready:
            return ""
        for entry in ready:
            queue.remove(entry)
        return ready[-1][0]

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[bytes]:
        with self._lock:
            self.synthesized.append(text)
        for clause in split_clauses(text) or [text]:
            seconds = max(0.3, SECONDS_PER_WORD * len(clause.split()) / max(speed, 0.1))
            size = int(seconds * OUTPUT_SAMPLE_RATE) * 2
            repeats = size // len(self._voice) + 1
            yield (self._voice * repeats)[:size]

    def voices(self) -> list[str]:
        return list(FAKE_VOICES)


def silence(seconds: float) -> bytes:
    return np.zeros(int(seconds * OUTPUT_SAMPLE_RATE), dtype="<i2").tobytes()
