"""LLM calls through named profiles. Cloud use is always explicit; there is no automatic fallback."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from crewquarters._models import WireModel
from crewquarters._transport import BrokerClient
from crewquarters.errors import InvalidInput, PermissionDenied, error_from_response

ROLES = frozenset({"system", "user", "assistant"})


class Usage(WireModel):
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ChatResult:
    text: str
    structured: Any
    parsed: Any
    usage: Usage
    finish_reason: str
    provider: str
    model: str
    locality: str
    latency_ms: int
    request_id: str


def _parse(data: dict[str, Any], response_model: type[BaseModel] | None) -> ChatResult:
    text = str(data.get("text", ""))
    structured = data.get("structured")
    parsed: Any = None
    if response_model is not None:
        source = structured
        if source is None:
            try:
                source = json.loads(text)
            except ValueError:
                source = None
        try:
            parsed = response_model.model_validate(source)
        except ValidationError as exc:
            raise InvalidInput(
                f"model output does not match {response_model.__name__}",
                code="STRUCTURED_OUTPUT_INVALID",
                details={"raw": text[:4000], "errors": exc.errors(include_url=False, include_input=False)},
            ) from exc
    return ChatResult(
        text=text,
        structured=structured,
        parsed=parsed,
        usage=Usage.model_validate(data.get("usage", {})),
        finish_reason=str(data.get("finishReason", "stop")),
        provider=str(data.get("provider", "")),
        model=str(data.get("model", "")),
        locality=str(data.get("locality", "")),
        latency_ms=int(data.get("latencyMs", 0)),
        request_id=str(data.get("requestId", "")),
    )


class ChatStream:
    """Async iterator of text deltas; ``result`` holds the final ChatResult after iteration."""

    def __init__(self, transport: BrokerClient, body: dict[str, Any], response_model: type[BaseModel] | None):
        self._transport = transport
        self._body = body
        self._response_model = response_model
        self.result: ChatResult | None = None

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        async for event, data in self._transport.stream_sse(
            "/llm/chat:stream", operation="llm.stream", json=self._body, read_timeout=300
        ):
            if event == "delta":
                yield str(data.get("text", ""))
            elif event == "done":
                self.result = _parse(data, self._response_model)
            elif event == "error":
                error = data.get("error", {}) if isinstance(data.get("error"), dict) else {}
                raise error_from_response(500, data, str(error.get("requestId") or ""))


class LLMClient:
    def __init__(self, transport: BrokerClient, granted_profiles: Sequence[str]) -> None:
        self._transport = transport
        self.granted_profiles = tuple(granted_profiles)

    def resolve_profile(self, profile: str) -> str:
        if profile in self.granted_profiles:
            return profile
        if profile.startswith("cloud."):
            raise PermissionDenied(
                f"cloud profile {profile} is not granted; cloud use must be approved explicitly"
            )
        variants = [p for p in self.granted_profiles if p.startswith(profile + ".")]
        if len(variants) == 1:
            return variants[0]
        if not variants:
            raise PermissionDenied(
                f"no granted profile matches {profile}; granted: {list(self.granted_profiles)}"
            )
        raise PermissionDenied(f"{profile} is ambiguous; name one of {variants}")

    def _body(
        self,
        profile: str,
        messages: Sequence[Mapping[str, str]],
        temperature: float | None,
        max_output_tokens: int | None,
        response_schema: dict[str, Any] | None,
        response_model: type[BaseModel] | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        normalised = []
        for message in messages:
            role, content = message.get("role"), message.get("content")
            if role not in ROLES or not isinstance(content, str):
                raise InvalidInput(f"messages need a role in {sorted(ROLES)} and string content")
            normalised.append({"role": role, "content": content})
        if not normalised:
            raise InvalidInput("messages must not be empty")
        body: dict[str, Any] = {"profile": self.resolve_profile(profile), "messages": normalised}
        if temperature is not None:
            body["temperature"] = temperature
        if max_output_tokens is not None:
            body["maxOutputTokens"] = max_output_tokens
        schema = response_model.model_json_schema() if response_model is not None else response_schema
        if schema is not None:
            body["responseSchema"] = schema
        body["tools"] = []
        if idempotency_key is not None:
            body["idempotencyKey"] = idempotency_key
        return body

    async def chat(
        self,
        profile: str,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        response_model: type[BaseModel] | None = None,
        idempotency_key: str | None = None,
    ) -> ChatResult:
        body = self._body(
            profile,
            messages,
            temperature,
            max_output_tokens,
            response_schema,
            response_model,
            idempotency_key,
        )
        data = await self._transport.request(
            "POST",
            "/llm/chat",
            operation="llm.chat",
            idempotent=idempotency_key is not None,
            json=body,
            read_timeout=300,
        )
        return _parse(data, response_model)

    def stream(
        self,
        profile: str,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        response_model: type[BaseModel] | None = None,
    ) -> ChatStream:
        body = self._body(
            profile, messages, temperature, max_output_tokens, response_schema, response_model, None
        )
        return ChatStream(self._transport, body, response_model)
