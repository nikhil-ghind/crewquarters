from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from crewquarters_speech.audio import (
    InvalidAudio,
    decode_wav,
    pcm16,
    resample,
    split_clauses,
    wav_header,
)


def make_wav(rate: int, seconds: float, width: int = 2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(rate * seconds) * width)
    return out.getvalue()


def test_decode_wav_returns_float_samples_and_rate() -> None:
    samples, rate = decode_wav(make_wav(8000, 0.5))
    assert rate == 8000 and samples.dtype == np.float32 and len(samples) == 4000


def test_decode_rejects_non_wav_and_non_16_bit() -> None:
    with pytest.raises(InvalidAudio):
        decode_wav(b"garbage")
    with pytest.raises(InvalidAudio):
        decode_wav(make_wav(16000, 0.1, width=1))


def test_resample_changes_length_by_the_rate_ratio() -> None:
    x = np.zeros(8000, dtype=np.float32)
    assert len(resample(x, 8000, 16000)) == 16000
    assert resample(x, 16000, 16000) is x


def test_pcm16_clips_and_packs_little_endian() -> None:
    packed = pcm16(np.array([0.0, 2.0, -2.0], dtype=np.float32))
    assert packed == b"\x00\x00\xff\x7f\x01\x80"


def test_wav_header_for_streaming() -> None:
    header = wav_header(24000)
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE" and len(header) == 44


def test_split_clauses_keeps_at_least_three_words_per_clause() -> None:
    text = "Hi Asha, this is Sam, calling for Acme Dental. Do you have a minute?"
    assert split_clauses(text) == [
        "Hi Asha, this is Sam,",
        "calling for Acme Dental.",
        "Do you have a minute?",
    ]
    assert split_clauses("Okay.") == ["Okay."]
    assert split_clauses("   ") == []
