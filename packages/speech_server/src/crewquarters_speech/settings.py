"""Speech-server settings, read from CREWQ_SPEECH_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _names(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(name.strip() for name in value.split(",") if name.strip())


@dataclass(frozen=True)
class SpeechSettings:
    engine: str = "fake"  # "fake" (deterministic, no models) or "sherpa"
    models_dir: Path = Path("/models")
    stt_model: str = "parakeet-tdt-0.6b-v2-int8"
    tts_model: str = "kokoro-en-v0_19"
    threads: int = 4
    # Profile variants this server answers for (the model gateway routes them here).
    stt_profiles: tuple[str, ...] = ("local.stt.small",)
    tts_profiles: tuple[str, ...] = ("local.tts.small",)
    default_voice: str = "af_sarah"

    @classmethod
    def from_env(cls) -> SpeechSettings:
        env = os.environ.get
        return cls(
            engine=env("CREWQ_SPEECH_ENGINE", "fake"),
            models_dir=Path(env("CREWQ_SPEECH_MODELS_DIR", "/models")),
            stt_model=env("CREWQ_SPEECH_STT_MODEL", cls.stt_model),
            tts_model=env("CREWQ_SPEECH_TTS_MODEL", cls.tts_model),
            threads=int(env("CREWQ_SPEECH_THREADS", "4")),
            stt_profiles=_names(env("CREWQ_SPEECH_STT_PROFILES"), cls.stt_profiles),
            tts_profiles=_names(env("CREWQ_SPEECH_TTS_PROFILES"), cls.tts_profiles),
            default_voice=env("CREWQ_SPEECH_VOICE", cls.default_voice),
        )
