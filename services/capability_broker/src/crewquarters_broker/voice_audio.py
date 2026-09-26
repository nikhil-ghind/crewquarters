"""Telephone audio for the realtime voice loop: G.711 mu-law, resampling, WAV, and an
energy-based voice activity detector.

Twilio Media Streams carry 8 kHz mono mu-law in 20 ms frames (160 bytes). The speech-to-text
model takes any WAV; the text-to-speech model produces 16-bit PCM at 24 kHz.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field

import numpy as np

TELEPHONE_RATE = 8000
FRAME_MS = 20
FRAME_SAMPLES = TELEPHONE_RATE * FRAME_MS // 1000  # 160

_BIAS = 0x84
_CLIP = 32635


def _decode_table() -> np.ndarray:
    codes = ~np.arange(256, dtype=np.int32) & 0xFF
    sign = codes & 0x80
    exponent = (codes >> 4) & 0x07
    mantissa = codes & 0x0F
    magnitude = ((mantissa << 3) + _BIAS) << exponent
    return np.where(sign != 0, _BIAS - magnitude, magnitude - _BIAS).astype(np.int16)


_DECODE = _decode_table()


def ulaw_decode(data: bytes) -> np.ndarray:
    """mu-law bytes -> int16 samples."""
    return _DECODE[np.frombuffer(data, dtype=np.uint8)]


def ulaw_encode(samples: np.ndarray) -> bytes:
    """int16 samples -> mu-law bytes (ITU-T G.711)."""
    x = samples.astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    magnitude = np.minimum(np.abs(x), _CLIP) + _BIAS
    exponent = np.floor(np.log2(np.maximum(magnitude, 1))).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    codes: np.ndarray = (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8)
    return codes.tobytes()


def _lowpass(cutoff: float, taps: int = 31) -> np.ndarray:
    """Windowed-sinc low-pass filter; ``cutoff`` is a fraction of the input rate."""
    n = np.arange(taps) - (taps - 1) / 2
    kernel: np.ndarray = np.sinc(2 * cutoff * n) * np.hamming(taps)
    normalized: np.ndarray = kernel / kernel.sum()
    return normalized


_DOWN_24K = _lowpass(3400 / 24000)


class Downsampler:
    """24 kHz int16 -> 8 kHz int16, streaming (keeps filter history between chunks)."""

    def __init__(self) -> None:
        self._history = np.zeros(len(_DOWN_24K) - 1, dtype=np.float64)
        self._phase = 0

    def feed(self, pcm24k: bytes) -> np.ndarray:
        x = np.frombuffer(pcm24k, dtype="<i2").astype(np.float64)
        if x.size == 0:
            return np.zeros(0, dtype=np.int16)
        joined = np.concatenate([self._history, x])
        filtered = np.convolve(joined, _DOWN_24K, mode="valid")
        self._history = joined[-(len(_DOWN_24K) - 1) :]
        out = filtered[self._phase :: 3]
        self._phase = (self._phase - len(filtered)) % 3
        return np.clip(out, -32768, 32767).astype(np.int16)


def wav_bytes(samples: np.ndarray, rate: int = TELEPHONE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return out.getvalue()


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


@dataclass
class VoiceActivityDetector:
    """Frame-by-frame speech segmentation on 20 ms telephone frames.

    A frame is voiced when its RMS is above both an absolute floor and a multiple of the
    tracked noise floor. Speech starts after ``start_frames`` voiced frames in a row and ends
    after ``end_silence_ms`` of unvoiced frames; utterances shorter than ``min_speech_ms``
    are dropped and longer ones are cut at ``max_speech_ms``.
    """

    min_rms: float = 600.0
    noise_factor: float = 3.0
    start_frames: int = 3
    end_silence_ms: int = 700
    min_speech_ms: int = 300
    max_speech_ms: int = 20000
    noise_floor: float = 200.0
    speaking: bool = False
    _run: int = 0
    _silence: int = 0
    _frames: list[np.ndarray] = field(default_factory=list)
    _pending: list[np.ndarray] = field(default_factory=list)

    def voiced(self, frame: np.ndarray) -> bool:
        level = rms(frame)
        is_voiced = level > max(self.min_rms, self.noise_factor * self.noise_floor)
        if not is_voiced and not self.speaking:
            # Track the background level slowly, only outside speech.
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * level
        return is_voiced

    def feed(self, frame: np.ndarray) -> tuple[str | None, np.ndarray | None]:
        """Returns ("start", None) when speech begins, ("end", samples) with a complete
        utterance, or (None, None)."""
        is_voiced = self.voiced(frame)
        if not self.speaking:
            if is_voiced:
                self._run += 1
                self._pending.append(frame)
                if self._run >= self.start_frames:
                    self.speaking = True
                    self._frames = self._pending
                    self._pending = []
                    self._silence = 0
                    return "start", None
            else:
                self._run = 0
                self._pending = []
            return None, None
        self._frames.append(frame)
        self._silence = 0 if is_voiced else self._silence + 1
        spoken_ms = len(self._frames) * FRAME_MS
        if self._silence * FRAME_MS >= self.end_silence_ms or spoken_ms >= self.max_speech_ms:
            samples = np.concatenate(self._frames)
            voiced_ms = spoken_ms - self._silence * FRAME_MS
            self.reset()
            if voiced_ms < self.min_speech_ms:
                return None, None
            return "end", samples
        return None, None

    def reset(self) -> None:
        self.speaking = False
        self._run = 0
        self._silence = 0
        self._frames = []
        self._pending = []
