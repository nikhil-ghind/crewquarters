"""Streaming text-to-speech server for VoXtream (herimor/voxtream), run as a model container.

The runtime daemon starts it like any other model server (``catalog/models/dgx/
local.tts.voxtream.json``): weights mounted read-only at ``--model-path``, no network
except the internal model network, and the model gateway as the only client.

Routes:

    GET  /health                   200 once the model is loaded and warmed up, else 503
    GET  /v1/models                the served model name once ready, else 503 (the gateway's
                                   readiness check, same shape as vLLM's)
    GET  /v1/voices                the bundled voices
    POST /v1/audio/speech          {"input", "voice", "format": "wav"|"pcm"} -> audio
                                   (pcm: 16-bit mono at 24 kHz, streamed as generated)
    WS   /v1/audio/speech/stream   ?voice=  client -> {"type": "text", "text"} ... then
                                   {"type": "end"} or {"type": "cancel"}; server -> binary
                                   PCM frames (80 ms each), then {"type": "done", ...}

Input streaming (text arriving word by word from an LLM) is VoXtream's "full-stream"
mode: speech starts before the sentence is complete. One stream is generated at a time;
others wait for the GPU lock.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import logging
import os
import queue
import re
import threading
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("cq-tts")
ASSETS = Path(os.environ.get("CQ_TTS_ASSETS", "/opt/cq-tts"))
MAX_INPUT_CHARS = 1000
END = object()

# Sample voices bundled from the VoXtream repository (assets/app), with the transcripts
# VoXtream needs for prompt alignment.
VOICES: dict[str, dict[str, str]] = {
    "female": {
        "file": "female.wav",
        "label": "Sample voice A (VoXtream repository)",
        "text": "I would certainly anticipate some pushback whereas most people know if you "
        "followed my work.",
    },
    "male": {
        "file": "male.wav",
        "label": "Sample voice B (VoXtream repository)",
        "text": "You could take the easy route or a situation that makes sense which a lot of "
        "you do",
    },
}


def words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def pcm16(frame: np.ndarray) -> bytes:
    return (np.clip(frame, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return out.getvalue()


class Synthesizer:
    """Owns the VoXtream generator; ``stream`` runs on a worker thread under one lock."""

    def __init__(self, model_path: Path) -> None:
        import voxtream.generator as vg

        # herimor/voxtream files come from the daemon-managed model directory; everything
        # else resolves from the image's pinned offline Hugging Face cache.
        original = vg.hf_hub_download
        config = json.loads((Path(__file__).parent / "generator.json").read_text())

        def hf_hub_download(repo_id: str, filename: str, **kwargs: Any) -> str:
            if repo_id == config["model_repo"]:
                return str(model_path / filename)
            return str(original(repo_id, filename, **kwargs))

        vg.hf_hub_download = hf_hub_download
        self.config = vg.SpeechGeneratorConfig(**config)
        self.generator = vg.SpeechGenerator(self.config)
        self.sample_rate = int(self.config.mimi_sr)
        self.lock = threading.Lock()
        self.ready = False

    def warm_up(self) -> None:
        for voice in VOICES:
            for _ in self.stream(voice, "Warming up the voice.", threading.Event()):
                pass
            for _ in self.stream(voice, iter(words("And streaming text.")), threading.Event()):
                pass
        self.ready = True

    def stream(
        self, voice: str, text: str | Iterator[str], cancel: threading.Event
    ) -> Iterator[np.ndarray]:
        """A complete string is synthesized as one utterance; an iterator of words uses
        VoXtream's full-stream mode (speech starts before the text is complete)."""
        spec = VOICES[voice]
        with self.lock, torch.inference_mode():
            frames = self.generator.generate_stream(
                prompt_text=spec["text"],
                prompt_audio_path=ASSETS / "voices" / spec["file"],
                text=text if isinstance(text, str) else (w for w in text),
            )
            for frame, _ in frames:
                if cancel.is_set():
                    frames.close()
                    return
                yield frame


def create_app(synth: Synthesizer, served_model_name: str = "voxtream") -> FastAPI:
    app = FastAPI(title="Crewquarters TTS (VoXtream)", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> Response:
        if not synth.ready:
            return JSONResponse({"status": "loading"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/v1/models")
    async def models() -> Response:
        if not synth.ready:
            return JSONResponse({"status": "loading"}, status_code=503)
        return JSONResponse(
            {"object": "list", "data": [{"id": served_model_name, "object": "model"}]}
        )

    @app.get("/v1/voices")
    async def voices() -> dict[str, Any]:
        return {
            "voices": [{"id": k, "label": v["label"]} for k, v in VOICES.items()],
            "sampleRate": synth.sample_rate,
        }

    @app.post("/v1/audio/speech", response_model=None)
    async def speech(body: dict[str, Any]) -> Response:
        text = str(body.get("input") or "").strip()
        voice = str(body.get("voice") or "female")
        fmt = str(body.get("format") or "wav")
        if not text or len(text) > MAX_INPUT_CHARS:
            return _error(422, "INVALID_INPUT", f"input must be 1-{MAX_INPUT_CHARS} characters.")
        if voice not in VOICES:
            return _error(422, "UNKNOWN_VOICE", f"voice must be one of {sorted(VOICES)}.")
        if fmt not in ("wav", "pcm"):
            return _error(422, "INVALID_FORMAT", "format must be wav or pcm.")
        cancel = threading.Event()
        chunks = _background(synth.stream(voice, text, cancel))
        headers = {"X-Sample-Rate": str(synth.sample_rate)}
        if fmt == "pcm":

            async def body_iter() -> Any:
                try:
                    async for frame in chunks:
                        yield pcm16(frame)
                finally:
                    cancel.set()

            return StreamingResponse(body_iter(), media_type="audio/L16", headers=headers)
        pcm = b"".join([pcm16(frame) async for frame in chunks])
        return Response(wav_bytes(pcm, synth.sample_rate), media_type="audio/wav", headers=headers)

    @app.websocket("/v1/audio/speech/stream")
    async def speech_stream(ws: WebSocket, voice: str = "female") -> None:
        await ws.accept()
        if voice not in VOICES:
            await ws.send_json({"type": "error", "code": "UNKNOWN_VOICE"})
            await ws.close()
            return
        incoming: queue.Queue[Any] = queue.Queue()
        cancel = threading.Event()

        def text_words() -> Iterator[str]:
            while True:
                item = incoming.get()
                if item is END:
                    return
                yield item

        async def reader() -> None:
            try:
                while True:
                    message = await ws.receive_json()
                    kind = message.get("type")
                    if kind == "text":
                        for word in words(str(message.get("text") or "")):
                            incoming.put(word)
                    elif kind == "end":
                        incoming.put(END)
                    elif kind == "cancel":
                        cancel.set()
                        incoming.put(END)
                        return
            except (WebSocketDisconnect, RuntimeError, ValueError):
                cancel.set()
                incoming.put(END)

        read_task = asyncio.create_task(reader())
        started = time.perf_counter()
        first_ms: float | None = None
        samples = 0
        try:
            async for frame in _background(synth.stream(voice, text_words(), cancel)):
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000
                samples += len(frame)
                await ws.send_bytes(pcm16(frame))
            await ws.send_json(
                {
                    "type": "done",
                    "cancelled": cancel.is_set(),
                    "audioSeconds": round(samples / synth.sample_rate, 3),
                    "sampleRate": synth.sample_rate,
                }
            )
        except (WebSocketDisconnect, RuntimeError):
            cancel.set()
        finally:
            cancel.set()
            incoming.put(END)
            read_task.cancel()
            seconds = samples / synth.sample_rate
            log.info("stream voice=%s audio=%.2fs first=%s", voice, seconds, first_ms)
            with contextlib.suppress(RuntimeError):
                await ws.close()

    return app


async def _background(frames: Iterator[np.ndarray]) -> Any:
    """Run a blocking frame generator on a thread and yield its frames asynchronously."""
    loop = asyncio.get_running_loop()
    out: asyncio.Queue[Any] = asyncio.Queue()

    def work() -> None:
        try:
            for frame in frames:
                loop.call_soon_threadsafe(out.put_nowait, frame)
        except BaseException as exc:  # surfaced to the consumer
            loop.call_soon_threadsafe(out.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(out.put_nowait, END)

    threading.Thread(target=work, daemon=True).start()
    while True:
        item = await out.get()
        if item is END:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - internal model network
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--served-model-name", default="voxtream")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    synth = Synthesizer(args.model_path)
    app = create_app(synth, args.served_model_name)
    # Warm up in the background so /health answers 503 (not a refused connection) meanwhile.
    threading.Thread(target=synth.warm_up, daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", ws_max_size=1 << 20)


if __name__ == "__main__":
    main()
