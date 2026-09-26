"""A deterministic speech engine for development and tests (no models).

Transcription returns the lines queued with ``queue_transcripts`` in order, so a simulated
callee can register what it is about to say and the agent "hears" exactly that. Synthesis repeats
a short bundled speech clip (real speech, so voice-activity detectors treat it as speech), about
0.3 s per word, and records the text it was asked to say.
"""

from __future__ import annotations

import threading
import wave
from collections import deque
from collections.abc import Iterator
from importlib import resources

import numpy as np

from crewquarters_speech.audio import OUTPUT_SAMPLE_RATE, Samples, split_clauses

SECONDS_PER_WORD = 0.3
FAKE_VOICES = ["af_sarah", "am_adam"]


def _clip() -> bytes:
    asset = resources.files("crewquarters_speech").joinpath("assets/fake_voice.wav")
    with asset.open("rb") as f, wave.open(f, "rb") as w:
        if w.getframerate() != OUTPUT_SAMPLE_RATE or w.getsampwidth() != 2:
            raise RuntimeError("fake_voice.wav must be 16-bit PCM at 24 kHz")
        return w.readframes(w.getnframes())


class FakeSpeechEngine:
    name = "fake"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transcripts: deque[str] = deque()
        self.synthesized: list[str] = []
        self.transcribed: list[str] = []
        self._voice = _clip()

    def queue_transcripts(self, lines: list[str]) -> None:
        with self._lock:
            self._transcripts.extend(lines)

    def transcribe(self, samples: Samples, sample_rate: int) -> str:
        with self._lock:
            text = self._transcripts.popleft() if self._transcripts else ""
            self.transcribed.append(text)
            return text

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
