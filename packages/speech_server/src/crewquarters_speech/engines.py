"""The speech engine interface and engine selection."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from crewquarters_speech.audio import Samples
from crewquarters_speech.settings import SpeechSettings


class SpeechEngine(Protocol):
    name: str

    def transcribe(self, samples: Samples, sample_rate: int) -> str:
        """Transcribe one utterance."""
        ...

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[bytes]:
        """Yield 16-bit little-endian mono PCM at 24 kHz, one chunk per clause."""
        ...

    def voices(self) -> list[str]: ...


def load_engine(settings: SpeechSettings) -> SpeechEngine:
    if settings.engine == "fake":
        from crewquarters_speech.fake import FakeSpeechEngine

        return FakeSpeechEngine()
    if settings.engine == "sherpa":
        from crewquarters_speech.sherpa import SherpaSpeechEngine

        return SherpaSpeechEngine(settings)
    raise ValueError(f"unknown speech engine {settings.engine!r}; use 'fake' or 'sherpa'")
