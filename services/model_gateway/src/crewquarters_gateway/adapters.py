"""LLM adapters behind one normalized request/response (PLAN.md section 8.4).

* ``LocalAdapter``: vLLM's OpenAI-compatible chat API (also the mock model server), its
  ``/v1/audio/transcriptions`` API for speech-to-text models, and the Crewquarters TTS
  server's ``/v1/audio/speech`` (buffered) and ``/v1/audio/speech/stream`` (WebSocket).
* ``InProcessMockAdapter``: deterministic replies with no server (unit tests).
* ``OpenAIAdapter``: OpenAI Responses API.
* ``AnthropicAdapter``: Anthropic Messages API through the official ``anthropic`` SDK.

Every adapter returns text, optional structured output, usage, finish reason,
provider/model, latency and request ID. Upstream failures use the broker-SDK contract's
codes (``packages/contracts/broker-sdk.openapi.yaml``): ``RATE_LIMITED`` (429),
``PROVIDER_ERROR`` (502, the provider rejected the request) and ``PROVIDER_UNAVAILABLE``
(503, unreachable or a provider 5xx). Features an adapter cannot honor fail with a
clear error instead of silently degrading; parameters a model rejects outright
(``temperature`` on Claude Opus 5) are dropped and reported in ``ignoredParameters``.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import io
import json
import math
import re
import time
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx
import websockets
from jsonschema import Draft202012Validator

from crewquarters_shared.errors import PlatformError

# Anthropic server-side refusal fallback (routes a declined request to Anthropic's
# recommended fallback model; see the claude-api reference, "fallbacks: default").
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that reject sampling parameters (temperature/top_p/top_k return 400).
NO_SAMPLING_PREFIXES = (
    "claude-opus-5",
    "claude-fable-5",
    "claude-sonnet-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
)


@dataclass
class ChatRequest:
    # {role, content}, plus ``images`` ([{mediaType, data}]) on user messages for vision models.
    messages: list[dict[str, Any]]
    max_output_tokens: int
    temperature: float | None = None
    response_schema: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class TranscriptionRequest:
    audio: bytes
    filename: str
    content_type: str
    language: str | None = None


@dataclass
class TranscriptionResult:
    text: str
    model: str
    latency_ms: int = 0
    audio_seconds: float | None = None
    language: str | None = None  # the requested language hint, if any

    def to_wire(self, model_id: str) -> dict[str, Any]:
        return {
            "modelId": model_id,
            "text": self.text,
            "language": self.language,
            "audioSeconds": self.audio_seconds,
            "latencyMs": self.latency_ms,
        }


@dataclass
class SpeechRequest:
    text: str
    voice: str


@dataclass
class SpeechResult:
    wav: bytes
    audio_seconds: float
    latency_ms: int


class SpeechStream(Protocol):
    """One streaming synthesis: text in (possibly word by word), 16-bit PCM frames out."""

    sample_rate: int

    async def send_text(self, text: str) -> None: ...

    async def end(self) -> None: ...

    async def cancel(self) -> None: ...

    def events(self) -> AsyncIterator[bytes | dict[str, Any]]: ...

    async def close(self) -> None: ...


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return out.getvalue()


MOCK_SPEECH_RATE = 24000
MOCK_FRAME_SAMPLES = 1920  # 80 ms, like VoXtream


def mock_speech_pcm(text: str) -> bytes:
    """The mock TTS model's audio: a quiet 440 Hz tone, 80 ms per word. Kept identical to
    ``catalog/models/dev/files/mock_openai_server.py`` (a test checks)."""
    frames = max(1, len(text.split())) * MOCK_FRAME_SAMPLES
    return b"".join(
        int(3000 * math.sin(2 * math.pi * 440 * i / MOCK_SPEECH_RATE)).to_bytes(
            2, "little", signed=True
        )
        for i in range(frames)
    )


@dataclass
class ChatResult:
    text: str
    finish_reason: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    request_id: str | None = None
    structured: Any = None
    structured_error: str | None = None
    refusal_category: str | None = None
    ignored_parameters: list[str] = field(default_factory=list)

    def to_wire(self, profile: str) -> dict[str, Any]:
        """The contract's ChatResponse (plus ``profile`` and ``ignoredParameters``). A
        missing provider request ID is filled with the gateway request ID by the route."""
        return {
            "profile": profile,
            "provider": self.provider,
            "model": self.model,
            "locality": "local" if self.provider == "local" else "cloud",
            "text": self.text,
            "structured": self.structured,
            "finishReason": normalize_finish_reason(self.finish_reason),
            "usage": {"inputTokens": self.input_tokens, "outputTokens": self.output_tokens},
            "latencyMs": max(0, self.latency_ms),
            "requestId": self.request_id,
            "ignoredParameters": self.ignored_parameters,
        }


FINISH_REASONS = frozenset({"stop", "length", "content_filter", "error"})
_FINISH_ALIASES = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "completed": "stop",
    "max_tokens": "length",
    "max_output_tokens": "length",
    "model_context_window_exceeded": "length",
    "safety": "content_filter",
}


def normalize_finish_reason(reason: str | None) -> str:
    """Map provider finish reasons onto the contract enum; unknown values become ``error``."""
    value = (reason or "stop").lower()
    value = _FINISH_ALIASES.get(value, value)
    return value if value in FINISH_REASONS else "error"


class Adapter(Protocol):
    provider: str

    async def chat(self, request: ChatRequest) -> ChatResult: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]: ...


def _openai_message(message: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-compatible message: images become ``image_url`` data-URL content parts."""
    if not message.get("images"):
        return {"role": message["role"], "content": message["content"]}
    parts: list[dict[str, Any]] = [{"type": "text", "text": message["content"]}]
    parts += [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image['mediaType']};base64,{image['data']}"},
        }
        for image in message["images"]
    ]
    return {"role": message["role"], "content": parts}


def _reject_tools(request: ChatRequest) -> None:
    if request.tools:
        raise PlatformError(
            "UNSUPPORTED_FEATURE",
            "Tool calling is not part of the v1 common request subset.",
            422,
        )


def validate_structured(text: str, schema: dict[str, Any]) -> Any:
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise PlatformError(
            "STRUCTURED_OUTPUT_INVALID", "The model did not return valid JSON.", 502
        ) from exc
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(value)][:5]
    if errors:
        raise PlatformError(
            "STRUCTURED_OUTPUT_INVALID",
            "The model's JSON does not match the requested schema.",
            502,
            {"errors": errors},
        )
    return value


def try_structured(text: str, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """Parse and validate without raising: usage is recorded before the caller fails."""
    try:
        return validate_structured(text, schema), None
    except PlatformError as exc:
        return None, exc.message


def provider_status_error(provider: str, status: int) -> PlatformError:
    """Never echo the provider's body: it may contain prompt text or key fragments."""
    details = {"provider": provider, "providerStatus": status}
    if status == 429:
        return PlatformError("RATE_LIMITED", f"{provider} rate limited the request.", 429, details)
    if status >= 500:
        return PlatformError("PROVIDER_UNAVAILABLE", f"{provider} returned {status}.", 503, details)
    return PlatformError(
        "PROVIDER_ERROR", f"{provider} rejected the request ({status}).", 502, details
    )


def provider_unreachable(provider: str, exc: BaseException) -> PlatformError:
    return PlatformError(
        "PROVIDER_UNAVAILABLE",
        f"{provider} unreachable: {type(exc).__name__}",
        503,
        {"provider": provider},
    )


def _http_error(provider: str, exc: Exception) -> PlatformError:
    if isinstance(exc, httpx.HTTPStatusError):
        return provider_status_error(provider, exc.response.status_code)
    return provider_unreachable(provider, exc)


# --- Local (vLLM / mock server) ---------------------------------------------------------


class LocalAdapter:
    provider = "local"

    def __init__(self, base_url: str, served_model: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.served_model = served_model
        self.timeout = timeout

    def _body(self, request: ChatRequest, stream: bool) -> dict[str, Any]:
        _reject_tools(request)
        body: dict[str, Any] = {
            "model": self.served_model,
            "messages": [_openai_message(m) for m in request.messages],
            "max_tokens": request.max_output_tokens,
            "stream": stream,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": request.response_schema},
            }
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    async def chat(self, request: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions", json=self._body(request, False)
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _http_error("Local model", exc) from exc
        data = response.json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        usage = data.get("usage") or {}
        result = ChatResult(
            text=text,
            finish_reason=choice.get("finish_reason") or "stop",
            provider=self.provider,
            model=data.get("model", self.served_model),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=data.get("id"),
        )
        if request.response_schema is not None:
            result.structured, result.structured_error = try_structured(
                text, request.response_schema
            )
        return result

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        parts: list[str] = []
        usage: dict[str, Any] = {}
        finish = "stop"
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout) as client,
                client.stream(
                    "POST", f"{self.base_url}/v1/chat/completions", json=self._body(request, True)
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices", []):
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            parts.append(delta)
                            yield {"type": "delta", "text": delta}
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as exc:
            raise _http_error("Local model", exc) from exc
        text = "".join(parts)
        result = ChatResult(
            text=text,
            finish_reason=finish,
            provider=self.provider,
            model=self.served_model,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        if request.response_schema is not None:
            result.structured, result.structured_error = try_structured(
                text, request.response_schema
            )
        yield {"type": "result", "result": result}

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """vLLM's OpenAI-compatible whole-file transcription (multipart upload)."""
        started = time.perf_counter()
        data = {"model": self.served_model, "response_format": "json"}
        if request.language:
            data["language"] = request.language
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/audio/transcriptions",
                    data=data,
                    files={"file": (request.filename, request.audio, request.content_type)},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _http_error("Local model", exc) from exc
        body = response.json()
        usage = body.get("usage") or {}
        seconds = usage.get("seconds") if usage.get("type") == "duration" else None
        return TranscriptionResult(
            text=str(body.get("text") or "").strip(),
            model=self.served_model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            audio_seconds=float(seconds) if seconds is not None else None,
            language=request.language,
        )

    async def speak(self, request: SpeechRequest) -> SpeechResult:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/audio/speech",
                    json={"input": request.text, "voice": request.voice, "format": "wav"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _http_error("Local model", exc) from exc
        with wave.open(io.BytesIO(response.content)) as w:
            seconds = w.getnframes() / w.getframerate()
        return SpeechResult(
            response.content, round(seconds, 3), int((time.perf_counter() - started) * 1000)
        )

    async def open_speech_stream(self, voice: str) -> SpeechStream:
        url = self.base_url.replace("http://", "ws://", 1) + "/v1/audio/speech/stream"
        try:
            ws = await websockets.connect(f"{url}?voice={voice}", max_size=1 << 20, open_timeout=10)
        except (OSError, websockets.WebSocketException) as exc:
            raise provider_unreachable("Local model", exc) from exc
        return _ServerSpeechStream(ws)


class _ServerSpeechStream:
    """A streaming synthesis on the TTS server's WebSocket."""

    sample_rate = 24000

    def __init__(self, ws: Any) -> None:
        self.ws = ws

    async def _send(self, message: dict[str, Any]) -> None:
        with contextlib.suppress(websockets.ConnectionClosed):
            await self.ws.send(json.dumps(message))

    async def send_text(self, text: str) -> None:
        await self._send({"type": "text", "text": text})

    async def end(self) -> None:
        await self._send({"type": "end"})

    async def cancel(self) -> None:
        await self._send({"type": "cancel"})

    async def events(self) -> AsyncIterator[bytes | dict[str, Any]]:
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    yield message
                    continue
                event = json.loads(message)
                if event.get("type") == "done":
                    self.sample_rate = int(event.get("sampleRate") or self.sample_rate)
                yield event
                if event.get("type") in ("done", "error"):
                    return
        except websockets.ConnectionClosed:
            return

    async def close(self) -> None:
        await self.ws.close()


class _MockSpeechStream:
    """In-process mock: after ``end`` (or ``cancel``), emits the mock audio for the text
    received so far in 80 ms frames, then ``done``."""

    sample_rate = MOCK_SPEECH_RATE

    def __init__(self) -> None:
        self.words: list[str] = []
        self.finished = asyncio.Event()
        self.cancelled = False

    async def send_text(self, text: str) -> None:
        self.words.extend(text.split())

    async def end(self) -> None:
        self.finished.set()

    async def cancel(self) -> None:
        self.cancelled = True
        self.finished.set()

    async def events(self) -> AsyncIterator[bytes | dict[str, Any]]:
        await self.finished.wait()
        pcm = b"" if self.cancelled else mock_speech_pcm(" ".join(self.words))
        step = MOCK_FRAME_SAMPLES * 2
        for i in range(0, len(pcm), step):
            if self.cancelled:
                break
            yield pcm[i : i + step]
            await asyncio.sleep(0)
        yield {
            "type": "done",
            "cancelled": self.cancelled,
            "audioSeconds": round(len(pcm) / 2 / MOCK_SPEECH_RATE, 3),
            "sampleRate": MOCK_SPEECH_RATE,
        }

    async def close(self) -> None:
        return None


class InProcessMockAdapter:
    """Same behavior as ``catalog/models/dev/files/mock_openai_server.py`` without HTTP."""

    provider = "local"

    def __init__(self, served_model: str) -> None:
        self.served_model = served_model

    def _text(self, request: ChatRequest) -> str:
        _reject_tools(request)
        if request.response_schema is not None:
            return json.dumps(_mock_value(request.response_schema))
        last = next((m for m in reversed(request.messages) if m["role"] == "user"), {"content": ""})
        return mock_reply(str(last["content"]))

    async def chat(self, request: ChatRequest) -> ChatResult:
        text = self._text(request)
        result = ChatResult(
            text=text,
            finish_reason="stop",
            provider=self.provider,
            model=self.served_model,
            input_tokens=sum(len(m["content"].split()) for m in request.messages),
            output_tokens=len(text.split()),
        )
        if request.response_schema is not None:
            result.structured, result.structured_error = try_structured(
                text, request.response_schema
            )
        return result

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        result = await self.chat(request)
        for i, word in enumerate(result.text.split(" ")):
            yield {"type": "delta", "text": word if i == 0 else " " + word}
        yield {"type": "result", "result": result}

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(
            text=mock_transcript(request.filename, len(request.audio)),
            model=self.served_model,
            audio_seconds=1.0,
            language=request.language,
        )

    async def speak(self, request: SpeechRequest) -> SpeechResult:
        pcm = mock_speech_pcm(request.text)
        return SpeechResult(
            wav_bytes(pcm, MOCK_SPEECH_RATE), round(len(pcm) / 2 / MOCK_SPEECH_RATE, 3), 0
        )

    async def open_speech_stream(self, voice: str) -> SpeechStream:
        return _MockSpeechStream()


def mock_transcript(filename: str | None, size: int) -> str:
    """The mock model's transcript. Kept identical to
    ``catalog/models/dev/files/mock_openai_server.py`` (a test checks)."""
    return f"Mock transcript of {filename or 'audio'} ({size} bytes)."


# Knowledge-grounded messages (crewquarters_knowledge.service.format_context): a preamble,
# then <evidence><passage id=... document=... location=...>text</passage>...</evidence>.
_EVIDENCE_MARKERS = ("<evidence>", "UNTRUSTED EVIDENCE")
_FIRST_PASSAGE = re.compile(
    r"<passage\s+id=(?:\"([^\"]*)\"|'([^']*)')[^>]*>(.*?)</passage>", re.DOTALL
)
MOCK_SNIPPET_CHARS = 120


def mock_reply(content: str) -> str:
    """The mock model's plain-text reply to the last user message.

    Ordinary messages are echoed (``Mock reply to: ...``, bounded). A message carrying an
    evidence block gets a short answer that quotes at most a snippet of the first passage
    and cites it, and never repeats the preamble, the delimiters or the whole message.
    Kept identical to ``catalog/models/dev/files/mock_openai_server.py`` (a test checks).
    """
    if not any(marker in content for marker in _EVIDENCE_MARKERS):
        return f"Mock reply to: {content[:200]}"
    match = _FIRST_PASSAGE.search(content)
    if match is None:
        return "Mock answer: the evidence did not contain a passage to quote."
    citation = html.unescape(match.group(1) or match.group(2) or "")
    text = " ".join(html.unescape(match.group(3)).split())
    text = text.replace("<", "").replace(">", "")
    if len(text) > MOCK_SNIPPET_CHARS:
        text = text[:MOCK_SNIPPET_CHARS].rsplit(" ", 1)[0] + "..."
    return f'Mock answer from the knowledge base: "{text}" [{citation}]'


def _mock_value(schema: dict[str, Any]) -> Any:
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {k: _mock_value(props.get(k, {})) for k in schema.get("required", list(props))}
    return {"array": [], "integer": 0, "number": 0.0, "boolean": False, "null": None}.get(
        str(kind), "mock"
    )


# --- OpenAI (Responses API) ---------------------------------------------------------------


class OpenAIAdapter:
    provider = "openai"
    base_url = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport

    def _body(self, request: ChatRequest, stream: bool) -> dict[str, Any]:
        _reject_tools(request)
        system = "\n\n".join(m["content"] for m in request.messages if m["role"] == "system")
        body: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": m["role"], "content": m["content"]}
                for m in request.messages
                if m["role"] != "system"
            ],
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "stream": stream,
        }
        if system:
            body["instructions"] = system
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.response_schema is not None:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": request.response_schema,
                    "strict": False,
                }
            }
        return body

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.api_key}"},
            transport=self.transport,
        )

    @staticmethod
    def _text(data: dict[str, Any]) -> str:
        return "".join(
            part.get("text", "")
            for item in data.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )

    def _result(
        self, data: dict[str, Any], text: str, started: float, request_id: str | None
    ) -> ChatResult:
        usage = data.get("usage") or {}
        status = data.get("status", "completed")
        incomplete = (data.get("incomplete_details") or {}).get("reason")
        return ChatResult(
            text=text,
            finish_reason="length"
            if incomplete == "max_output_tokens"
            else ("stop" if status == "completed" else status),
            provider=self.provider,
            model=data.get("model", self.model),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_id,
        )

    async def verify(self) -> None:
        """A minimal authenticated call (list models) for the connection test."""
        try:
            async with self._client() as client:
                response = await client.get("/models")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _http_error("OpenAI", exc) from None

    async def chat(self, request: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        try:
            async with self._client() as client:
                response = await client.post("/responses", json=self._body(request, False))
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _http_error("OpenAI", exc) from exc
        data = response.json()
        result = self._result(data, self._text(data), started, response.headers.get("x-request-id"))
        if request.response_schema is not None:
            result.structured, result.structured_error = try_structured(
                result.text, request.response_schema
            )
        return result

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        parts: list[str] = []
        final: dict[str, Any] = {}
        request_id = None
        try:
            async with (
                self._client() as client,
                client.stream("POST", "/responses", json=self._body(request, True)) as response,
            ):
                response.raise_for_status()
                request_id = response.headers.get("x-request-id")
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        parts.append(event.get("delta", ""))
                        yield {"type": "delta", "text": event.get("delta", "")}
                    elif kind in ("response.completed", "response.incomplete"):
                        final = event.get("response", {})
                    elif kind in ("response.failed", "error"):
                        raise PlatformError(
                            "PROVIDER_ERROR", "OpenAI reported a failed response.", 502
                        )
        except httpx.HTTPError as exc:
            raise _http_error("OpenAI", exc) from exc
        result = self._result(final, "".join(parts), started, request_id)
        if request.response_schema is not None:
            result.structured, result.structured_error = try_structured(
                result.text, request.response_schema
            )
        yield {"type": "result", "result": result}


# --- Anthropic (Messages API via the official SDK) ------------------------------------------


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float,
        *,
        fallbacks: bool = True,
        client: anthropic.AsyncAnthropic | None = None,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.fallbacks = fallbacks
        self.client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def _params(self, request: ChatRequest) -> tuple[dict[str, Any], list[str]]:
        _reject_tools(request)
        ignored: list[str] = []
        system = "\n\n".join(m["content"] for m in request.messages if m["role"] == "system")
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "messages": [
                {"role": m["role"], "content": m["content"]}
                for m in request.messages
                if m["role"] in ("user", "assistant")
            ],
        }
        if system:
            params["system"] = system
        if request.temperature is not None:
            if self.model.startswith(NO_SAMPLING_PREFIXES):
                ignored.append("temperature")
            else:
                params["temperature"] = request.temperature
        if request.response_schema is not None:
            params["output_config"] = {
                "format": {"type": "json_schema", "schema": request.response_schema}
            }
        if self.fallbacks:
            params["betas"] = [ANTHROPIC_FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params, ignored

    def _result(self, message: Any, started: float, ignored: list[str]) -> ChatResult:
        text = "".join(block.text for block in message.content if block.type == "text")
        stop = message.stop_reason or ""
        finish = {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop"}.get(
            stop, stop or "stop"
        )
        result = ChatResult(
            text=text,
            finish_reason=finish,
            provider=self.provider,
            model=message.model,
            input_tokens=int(message.usage.input_tokens or 0),
            output_tokens=int(message.usage.output_tokens or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=getattr(message, "_request_id", None),
            ignored_parameters=ignored,
        )
        if stop == "refusal":  # check before trusting content; the caller raises MODEL_REFUSED
            result.refusal_category = getattr(
                getattr(message, "stop_details", None), "category", None
            )
        return result

    @staticmethod
    def _error(exc: Exception) -> PlatformError:
        if isinstance(exc, anthropic.APIStatusError):
            return provider_status_error("Anthropic", exc.status_code)
        return provider_unreachable("Anthropic", exc)

    async def verify(self) -> None:
        """A minimal authenticated call (list one model) for the connection test."""
        try:
            await self.client.models.list(limit=1)
        except anthropic.APIError as exc:
            raise self._error(exc) from None

    async def chat(self, request: ChatRequest) -> ChatResult:
        params, ignored = self._params(request)
        started = time.perf_counter()
        try:
            message = await self.client.beta.messages.create(**params)
        except anthropic.APIError as exc:
            raise self._error(exc) from exc
        result = self._result(message, started, ignored)
        if request.response_schema is not None:
            result.structured, result.structured_error = try_structured(
                result.text, request.response_schema
            )
        return result

    async def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        params, ignored = self._params(request)
        started = time.perf_counter()
        try:
            async with self.client.beta.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    yield {"type": "delta", "text": text}
                message = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise self._error(exc) from exc
        result = self._result(message, started, ignored)
        if request.response_schema is not None:
            result.structured, result.structured_error = try_structured(
                result.text, request.response_schema
            )
        yield {"type": "result", "result": result}
