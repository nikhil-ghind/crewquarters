"""Pinned speech models (sherpa-onnx builds), with download URLs and SHA-256 digests."""

from __future__ import annotations

from dataclasses import dataclass

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass(frozen=True)
class ModelArchive:
    name: str
    kind: str  # "stt" or "tts"
    url: str
    sha256: str
    directory: str
    files: tuple[str, ...]
    license: str


STT_MODELS: dict[str, ModelArchive] = {
    # NVIDIA Parakeet TDT 0.6B v2 (English), int8 — 0.15 s for 6 s of speech on an M3 Pro CPU.
    "parakeet-tdt-0.6b-v2-int8": ModelArchive(
        name="parakeet-tdt-0.6b-v2-int8",
        kind="stt",
        url=f"{RELEASES}/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
        sha256="157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad",
        directory="sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
        files=("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
        license="CC-BY-4.0 (NVIDIA)",
    ),
}

TTS_MODELS: dict[str, ModelArchive] = {
    # Kokoro 82M v0.19, fp32 — the int8 build is 2.4x slower on ARM CPUs (measured).
    "kokoro-en-v0_19": ModelArchive(
        name="kokoro-en-v0_19",
        kind="tts",
        url=f"{RELEASES}/tts-models/kokoro-en-v0_19.tar.bz2",
        sha256="912804855a04745fa77a30be545b3f9a5d15c4d66db00b88cbcd4921df605ac7",
        directory="kokoro-en-v0_19",
        files=("model.onnx", "voices.bin", "tokens.txt", "espeak-ng-data"),
        license="Apache-2.0",
    ),
}

# Speaker order inside kokoro-en-v0_19/voices.bin.
KOKORO_V019_VOICES = (
    "af",
    "af_bella",
    "af_nicole",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bf_isabella",
    "bm_george",
    "bm_lewis",
)
