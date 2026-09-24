"""Model gateway stand-in: profile routing to the mock rules or an OpenAI-compatible server."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from crewquarters_fake.errors import ApiError
from crewquarters_fake.llm_rules import RuleSet
from crewquarters_fake.settings import FakeSettings


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    locality: str
    provider: str
    model: str
    backend: str


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
        if name.startswith("local.general.") and self.settings.llm_base_url:
            model = self.settings.llm_model or parts[-1]
            return ProfileInfo(name, "local", "openai-compatible", model, "openai-compatible")
        if name.startswith("local."):
            return ProfileInfo(name, "local", "mock-local", f"mock-{parts[-1]}", "mock")
        if name.startswith("cloud.") and len(parts) >= 3:
            return ProfileInfo(name, "cloud", parts[1], ".".join(parts[2:]), "mock")
        raise ApiError(422, "INVALID_REQUEST", f"unknown profile {name}")

    async def complete(self, info: ProfileInfo, request: dict[str, Any]) -> Completion:
        messages = request["messages"]
        schema = request.get("responseSchema")
        if info.backend == "openai-compatible":
            return await self._openai_compatible(info, request)
        text, structured = self.rules.respond(messages, schema)
        rule = self.rules.match(messages, schema)
        if rule is not None and rule.delay_ms:
            import asyncio

            await asyncio.sleep(rule.delay_ms / 1000)
        prompt_words = sum(len(str(m.get("content", "")).split()) for m in messages)
        return Completion(text, structured, prompt_words, len(text.split()))

    async def _openai_compatible(self, info: ProfileInfo, request: dict[str, Any]) -> Completion:
        body: dict[str, Any] = {"model": info.model, "messages": request["messages"]}
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
        headers = (
            {"Authorization": f"Bearer {self.settings.llm_api_key}"} if self.settings.llm_api_key else {}
        )
        base = str(self.settings.llm_base_url).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=300) as http:
                response = await http.post(f"{base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiError(503, "MODEL_UNAVAILABLE", f"local model server unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ApiError(502, "PROVIDER_ERROR", f"local model server returned HTTP {response.status_code}")
        data = response.json()
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
