"""The real engine: sherpa-onnx with NVIDIA Parakeet (speech-to-text) and Kokoro (speech).

Runs on the CPU of a laptop or the GB10. Model files are loaded from ``models_dir`` (see
``crewq-speech download``) and are never fetched at request time.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from crewquarters_speech.audio import OUTPUT_SAMPLE_RATE, Samples, pcm16, resample, split_clauses
from crewquarters_speech.catalog import KOKORO_V019_VOICES, STT_MODELS, TTS_MODELS, ModelArchive
from crewquarters_speech.settings import SpeechSettings

STT_SAMPLE_RATE = 16000


class ModelsMissing(RuntimeError):
    pass


def model_path(models_dir: Path, archive: ModelArchive) -> Path:
    root = models_dir / archive.directory
    missing = [f for f in archive.files if not (root / f).exists()]
    if missing:
        raise ModelsMissing(
            f"{archive.name} is not in {models_dir} (missing {', '.join(missing)}); "
            "run `crewq-speech download` first"
        )
    return root


class SherpaSpeechEngine:
    name = "sherpa"

    def __init__(self, settings: SpeechSettings) -> None:
        import sherpa_onnx

        stt = model_path(settings.models_dir, STT_MODELS[settings.stt_model])
        tts = model_path(settings.models_dir, TTS_MODELS[settings.tts_model])
        self._recognizer: Any = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(stt / "encoder.int8.onnx"),
            decoder=str(stt / "decoder.int8.onnx"),
            joiner=str(stt / "joiner.int8.onnx"),
            tokens=str(stt / "tokens.txt"),
            num_threads=settings.threads,
            model_type="nemo_transducer",
        )
        kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(tts / "model.onnx"),
            voices=str(tts / "voices.bin"),
            tokens=str(tts / "tokens.txt"),
            data_dir=str(tts / "espeak-ng-data"),
        )
        self._tts: Any = sherpa_onnx.OfflineTts(
            sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro, num_threads=settings.threads)
            )
        )
        # One decode at a time per model: the ONNX sessions use all configured threads already.
        self._stt_lock = threading.Lock()
        self._tts_lock = threading.Lock()

    def transcribe(self, samples: Samples, sample_rate: int) -> str:
        audio = resample(samples, sample_rate, STT_SAMPLE_RATE)
        with self._stt_lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(STT_SAMPLE_RATE, audio)
            self._recognizer.decode_stream(stream)
            return str(stream.result.text).strip()

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[bytes]:
        speaker = KOKORO_V019_VOICES.index(voice) if voice in KOKORO_V019_VOICES else 3
        for clause in split_clauses(text):
            with self._tts_lock:
                audio = self._tts.generate(clause, sid=speaker, speed=speed)
            samples = np.asarray(audio.samples, dtype=np.float32)
            yield pcm16(resample(samples, int(audio.sample_rate), OUTPUT_SAMPLE_RATE))

    def voices(self) -> list[str]:
        return list(KOKORO_V019_VOICES)
