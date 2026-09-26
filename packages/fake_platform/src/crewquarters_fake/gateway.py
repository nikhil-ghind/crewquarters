"""Model gateway stand-in: profile routing to the mock rules or an OpenAI-compatible server."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from crewquarters_fake.errors import ApiError
from crewquarters_fake.llm_rules import RuleSet, content_text
from crewquarters_fake.settings import FakeSettings


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    locality: str
    provider: str
    model: str
    backend: str


@dataclass
class ChatReply:
    """An OpenAI-style assistant turn: text and/or tool calls."""

    text: str
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    finish_reason: str


# OpenAI chat parameters passed through to an OpenAI-compatible server (vLLM, Ollama, ...).
PASSTHROUGH = frozenset(
    {
        "messages",
        "temperature",
        "max_tokens",
        "max_completion_tokens",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "response_format",
        "top_p",
        "stop",
    }
)


@dataclass
class Completion:
    text: str
    structured: Any
    input_tokens: int
    output_tokens: int
    finish_reason: str = "stop"


class Gateway:
    def __init__(self, settings: FakeSettings) -> None:
        self.settings = settings
        self.rules = RuleSet([])
        self.cold_start_seconds = 0.0
        self.loaded: set[str] = set()
        self.cache: dict[tuple[str, str], dict[str, Any]] = {}
        self.log: list[dict[str, Any]] = []

    def profile(self, name: str) -> ProfileInfo:
        parts = name.split(".")
        if name.startswith(("local.general.", "local.vision.")) and self.settings.llm_base_url:
            model = self.settings.llm_model or parts[-1]
            return ProfileInfo(name, "local", "openai-compatible", model, "openai-compatible")
        if name.startswith("local."):
            return ProfileInfo(name, "local", "mock-local", f"mock-{parts[-1]}", "mock")
        if parts[0] in ("openai", "anthropic") and len(parts) >= 2:
            return ProfileInfo(name, "cloud", parts[0], ".".join(parts[1:]), "mock")
        raise ApiError(422, "INVALID_REQUEST", f"unknown profile {name}")

    async def complete(self, info: ProfileInfo, request: dict[str, Any]) -> Completion:
        messages = request["messages"]
        schema = request.get("responseSchema")
        if info.backend == "openai-compatible":
            return await self._openai_compatible(info, request)
        text, structured = self.rules.respond(messages, schema)
        rule = self.rules.match(messages, schema)
        if rule is not None and rule.delay_ms:
            await asyncio.sleep(rule.delay_ms / 1000)
        prompt_words = sum(len(str(m.get("content", "")).split()) for m in messages)
        return Completion(text, structured, prompt_words, len(text.split()))

    async def chat_completion(self, info: ProfileInfo, body: dict[str, Any]) -> ChatReply:
        """Answer an OpenAI chat-completions request (the broker's OpenAI-compatible facade)."""
        messages = body["messages"]
        if info.backend == "openai-compatible":
            upstream = {k: v for k, v in body.items() if k in PASSTHROUGH}
            data = await self._post_upstream({**upstream, "model": info.model, "stream": False})
            choice = data["choices"][0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            return ChatReply(
                str(message.get("content") or ""),
                list(message.get("tool_calls") or []),
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
                str(choice.get("finish_reason") or "stop"),
            )
        schema = None
        response_format = body.get("response_format") or {}
        if response_format.get("type") == "json_schema":
            schema = (response_format.get("json_schema") or {}).get("schema")
        reply = self.rules.reply(messages, schema)
        rule = self.rules.match(messages, schema)
        if rule is not None and rule.delay_ms:
            await asyncio.sleep(rule.delay_ms / 1000)
        tool_calls = []
        if reply.tool_call is not None:
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": str(reply.tool_call.get("name", "")),
                        "arguments": json.dumps(reply.tool_call.get("arguments") or {}),
                    },
                }
            )
        prompt_words = sum(len(content_text(m.get("content")).split()) for m in messages)
        return ChatReply(
            reply.text,
            tool_calls,
            prompt_words,
            max(1, len(reply.text.split())),
            "tool_calls" if tool_calls else "stop",
        )

    async def _post_upstream(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = (
            {"Authorization": f"Bearer {self.settings.llm_api_key}"}
            if self.settings.llm_api_key
            else {}
        )
        base = str(self.settings.llm_base_url).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=300) as http:
                response = await http.post(f"{base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiError(
                503, "MODEL_UNAVAILABLE", f"local model server unreachable: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise ApiError(
                502,
                "PROVIDER_ERROR",
                f"local model server returned HTTP {response.status_code}",
            )
        data: dict[str, Any] = response.json()
        return data

    async def _openai_compatible(self, info: ProfileInfo, request: dict[str, Any]) -> Completion:
        body: dict[str, Any] = {
            "model": info.model,
            "messages": [_openai_message(m) for m in request["messages"]],
        }
        if request.get("temperature") is not None:
            body["temperature"] = request["temperature"]
        if request.get("maxOutputTokens"):
            body["max_tokens"] = request["maxOutputTokens"]
        schema = request.get("responseSchema")
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": str(schema.get("title", "response")), "schema": schema},
            }
        data = await self._post_upstream(body)
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        structured = None
        if schema:
            try:
                structured = json.loads(text)
            except ValueError:
                structured = None
        usage = data.get("usage", {})
        finish = {"stop": "stop", "length": "length", "content_filter": "content_filter"}.get(
            choice.get("finish_reason", "stop"), "stop"
        )
        return Completion(
            text,
            structured,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
            finish,
        )


def _openai_message(message: dict[str, Any]) -> dict[str, Any]:
    """Like the real gateway: images become OpenAI ``image_url`` data-URL content parts."""
    if not message.get("images"):
        return {"role": message["role"], "content": message["content"]}
    parts: list[dict[str, Any]] = [{"type": "text", "text": message["content"]}]
    for image in message["images"]:
        url = f"data:{image['mediaType']};base64,{image['data']}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": message["role"], "content": parts}
