"""A real Crewquarters model gateway behind the fake platform's model facade.

With ``CREWQ_FAKE_GATEWAY_URL`` set (for example an SSH tunnel to a GB10 appliance's gateway), the
facade's chat completions, transcriptions, and speech use the appliance's models. The fake
authenticates as the capability broker does for a phone call: the service token plus the voice
client credential, with one voice holder for this process. The gateway's chat route takes no
tools (D13), so tool definitions are dropped and replies are text only.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from crewquarters_fake.errors import ApiError
from crewquarters_fake.gateway import ChatReply
from crewquarters_fake.llm_rules import content_text
from crewquarters_speech.audio import (
    OUTPUT_SAMPLE_RATE,
    InvalidAudio,
    decode_wav,
    pcm16,
    resample,
    wav_header,
)

CHUNK_BYTES = 9600  # 200 ms of 24 kHz 16-bit audio
log = logging.getLogger(__name__)


def plain_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """System, user, and assistant text only: tool calls and tool results are not supported."""
    plain = []
    for message in messages:
        role = message.get("role")
        text = content_text(message.get("content"))
        if role in {"system", "user", "assistant"} and text:
            plain.append({"role": str(role), "content": text})
    return plain


class CrewquartersGateway:
    def __init__(
        self,
        url: str,
        service_token: str,
        voice_token: str,
        *,
        stt_model: str,
        tts_model: str,
        voice: str = "female",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base = url.rstrip("/") + "/internal/v1"
        self.headers = {
            "Authorization": f"Bearer {service_token}",
            "x-voice-client-token": voice_token,
        }
        self.stt_model, self.tts_model, self.voice = stt_model, tts_model, voice
        self.holder = f"fake-{uuid.uuid4().hex[:12]}"
        self._transport = transport

    def _client(self, read_timeout: float = 300) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base,
            headers=self.headers,
            timeout=httpx.Timeout(30, read=read_timeout),
            transport=self._transport,
        )

    async def chat(self, profile: str, body: dict[str, Any]) -> ChatReply:
        request: dict[str, Any] = {
            "profile": profile,
            "messages": plain_messages(body["messages"]),
            "stream": True,
            "holder": {"type": "voice", "id": self.holder},
        }
        limit = body.get("max_completion_tokens") or body.get("max_tokens")
        if limit:
            request["maxOutputTokens"] = int(limit)
        if body.get("temperature") is not None:
            request["temperature"] = float(body["temperature"])
        parts: list[str] = []
        usage: dict[str, Any] = {}
        started = time.monotonic()
        try:
            async with (
                self._client() as http,
                http.stream("POST", "/llm/chat", json=request) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    _raise_for(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event.get("type") == "delta":
                        parts.append(str(event.get("text") or ""))
                    elif event.get("type") == "done":
                        usage = (event.get("response") or {}).get("usage") or {}
                    elif event.get("type") == "error":
                        raise ApiError(502, "PROVIDER_ERROR", f"gateway: {event.get('error')}")
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise ApiError(503, "MODEL_UNAVAILABLE", f"gateway unreachable: {detail}") from exc
        text = "".join(parts).strip()
        log.info("gateway chat %.2fs: %r", time.monotonic() - started, text)
        return ChatReply(
            text,
            [],
            int(usage.get("inputTokens", 0)),
            int(usage.get("outputTokens", 0)),
            "stop",
        )

    async def transcribe(
        self, audio: bytes, model: str, language: str | None, channel: str = ""
    ) -> str:
        params = {
            "modelId": self.stt_model,
            "holderId": self.holder,
            "language": language or "en",
            "filename": "utterance.wav",
        }
        started = time.monotonic()
        try:
            async with self._client() as http:
                response = await http.post(
                    "/audio/transcriptions",
                    params=params,
                    content=audio,
                    headers={"Content-Type": "audio/wav"},
                )
        except httpx.HTTPError as exc:
            raise ApiError(503, "MODEL_UNAVAILABLE", f"gateway unreachable: {exc}") from exc
        _raise_for(response)
        text = str(response.json().get("text") or "").strip()
        log.info("gateway speech-to-text %.2fs: %r", time.monotonic() - started, text)
        return text

    async def synthesize(
        self, model: str, text: str, voice: str | None, response_format: str, speed: float
    ) -> AsyncIterator[bytes]:
        body = {
            "modelId": self.tts_model,
            "input": text,
            "voice": self.voice,
            "holderId": self.holder,
        }
        started = time.monotonic()
        try:
            async with self._client() as http:
                response = await http.post("/audio/speech", json=body)
        except httpx.HTTPError as exc:
            raise ApiError(503, "MODEL_UNAVAILABLE", f"gateway unreachable: {exc}") from exc
        _raise_for(response)
        try:
            samples, rate = decode_wav(response.content)
        except InvalidAudio as exc:
            raise ApiError(502, "PROVIDER_ERROR", f"gateway speech: {exc}") from exc
        audio = pcm16(resample(samples, rate, OUTPUT_SAMPLE_RATE))
        log.info("gateway speech %.2fs: %r", time.monotonic() - started, text)
        if response_format == "wav":
            yield wav_header(OUTPUT_SAMPLE_RATE)
        for start in range(0, len(audio), CHUNK_BYTES):
            yield audio[start : start + CHUNK_BYTES]

    async def release(self) -> None:
        """Let the appliance unload the models this process held (leases also expire)."""
        try:
            async with self._client() as http:
                await http.delete(f"/leases/holders/manual/voice:{self.holder}")
        except httpx.HTTPError:
            pass


def _raise_for(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    status = response.status_code if response.status_code < 500 else 502
    code = str(error.get("code") or "PROVIDER_ERROR")
    raise ApiError(status, code, f"gateway: {error.get('message') or response.status_code}")
