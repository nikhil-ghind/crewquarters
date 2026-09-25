"""LLM adapters behind one normalized request/response (PLAN.md section 8.4).

* ``LocalAdapter``: vLLM's OpenAI-compatible chat API (also the mock model server).
* ``InProcessMockAdapter``: deterministic replies with no server (unit tests).
* ``OpenAIAdapter``: OpenAI Responses API.
* ``AnthropicAdapter``: Anthropic Messages API through the official ``anthropic`` SDK.

Every adapter returns text, optional structured output, usage, finish reason,
provider/model, latency and request ID. Features an adapter cannot honor fail with a
clear error instead of silently degrading; parameters a model rejects outright
(``temperature`` on Claude Opus 5) are dropped and reported in ``ignoredParameters``.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx
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
    messages: list[dict[str, str]]
    max_output_tokens: int
    temperature: float | None = None
    response_schema: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


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
        return {
            "profile": profile,
            "provider": self.provider,
            "model": self.model,
            "text": self.text,
            "structured": self.structured,
            "finishReason": self.finish_reason,
            "usage": {"inputTokens": self.input_tokens, "outputTokens": self.output_tokens},
            "latencyMs": self.latency_ms,
            "requestId": self.request_id,
            "ignoredParameters": self.ignored_parameters,
        }


class Adapter(Protocol):
    provider: str

    async def chat(self, request: ChatRequest) -> ChatResult: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]: ...


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


def _http_error(provider: str, exc: Exception) -> PlatformError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return PlatformError(
                "PROVIDER_RATE_LIMITED", f"{provider} rate limited the request.", 429
            )
        if status >= 500:
            return PlatformError("PROVIDER_UNAVAILABLE", f"{provider} returned {status}.", 502)
        return PlatformError(
            "PROVIDER_REJECTED", f"{provider} rejected the request ({status}).", 502
        )
    return PlatformError(
        "PROVIDER_UNAVAILABLE", f"{provider} unreachable: {type(exc).__name__}", 502
    )


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
            "messages": request.messages,
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
        return f"Mock reply to: {str(last['content'])[:200]}"

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
                            "PROVIDER_REJECTED", "OpenAI reported a failed response.", 502
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
    ) -> None:
        self.model = model
        self.fallbacks = fallbacks
        self.client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=2
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
        if isinstance(exc, anthropic.RateLimitError):
            return PlatformError(
                "PROVIDER_RATE_LIMITED", "Anthropic rate limited the request.", 429
            )
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code >= 500:
                return PlatformError(
                    "PROVIDER_UNAVAILABLE", f"Anthropic returned {exc.status_code}.", 502
                )
            return PlatformError(
                "PROVIDER_REJECTED", f"Anthropic rejected the request ({exc.status_code}).", 502
            )
        return PlatformError(
            "PROVIDER_UNAVAILABLE", f"Anthropic unreachable: {type(exc).__name__}", 502
        )

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
