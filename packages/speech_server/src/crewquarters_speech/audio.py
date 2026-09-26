"""Audio helpers: WAV decoding, resampling, 16-bit packing, streaming WAV headers, clause split."""

from __future__ import annotations

import io
import re
import struct
import wave

import numpy as np
import numpy.typing as npt

Samples = npt.NDArray[np.float32]
OUTPUT_SAMPLE_RATE = 24000  # what OpenAI-compatible clients expect for "pcm"


class InvalidAudio(ValueError):
    pass


def decode_wav(data: bytes) -> tuple[Samples, int]:
    """Decode a 16-bit PCM WAV (any sample rate; stereo is mixed down) to float32 samples."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError, struct.error) as exc:
        raise InvalidAudio(f"not a readable WAV file: {exc}") from exc
    if width != 2:
        raise InvalidAudio(f"expected 16-bit PCM, got {8 * width}-bit")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.float32)
    return samples, rate


def resample(samples: Samples, source_rate: int, target_rate: int) -> Samples:
    """Linear-interpolation resampling; good enough for speech models and telephony audio."""
    if source_rate == target_rate or len(samples) == 0:
        return samples
    length = round(len(samples) * target_rate / source_rate)
    positions = np.linspace(0, len(samples) - 1, num=length)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def pcm16(samples: Samples) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (np.round(clipped * 32767.0)).astype("<i2").tobytes()


def wav_header(sample_rate: int, channels: int = 1) -> bytes:
    """A WAV header for a stream of unknown length (sizes set to the maximum)."""
    byte_rate = sample_rate * channels * 2
    return (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, channels * 2, 16)
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
    )


_CLAUSE_END = re.compile(r"(?<=[,;:.!?…])\s+")


def split_clauses(text: str, min_words: int = 3) -> list[str]:
    """Split at commas and sentence ends so synthesis can stream clause by clause.

    Pieces shorter than ``min_words`` are joined to the next piece: a two-word clause costs almost
    as much fixed synthesis time as a sentence and sounds clipped on its own."""
    pieces = [p.strip() for p in _CLAUSE_END.split(text.strip()) if p.strip()]
    clauses: list[str] = []
    pending = ""
    for piece in pieces:
        pending = f"{pending} {piece}".strip()
        if len(pending.split()) >= min_words:
            clauses.append(pending)
            pending = ""
    if pending:
        if clauses and len(pending.split()) < min_words:
            clauses[-1] = f"{clauses[-1]} {pending}"
        else:
            clauses.append(pending)
    return clauses
