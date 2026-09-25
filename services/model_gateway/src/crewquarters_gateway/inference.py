"""Inference routing: authorization, profile resolution, leases, budgets, usage.

Callers:
* **Runs** (through the capability broker): send the agent's capability token in
  ``X-Capability-Token``. The gateway verifies it, confirms with the control API that
  the run is active and the token belongs to the current attempt, and requires the
  matching capability (``llm.profile:<variant>``, plus ``cloud.<provider>`` for cloud).
* **Chat** (the control API, service credential): names a chat holder. Chat is
  local-only.

Cloud routing is never automatic: a cloud profile must be requested explicitly, and an
enabled provider profile (Connections) must hold the key. The provider profile's
``allowedModels`` restrict which models ``<provider>.<name>`` may resolve to and its
``budgets`` (``dailyTokens``, ``perRunTokens``) apply on top of the gateway-wide limits.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_gateway.adapters import (
    Adapter,
    AnthropicAdapter,
    ChatRequest,
    ChatResult,
    InProcessMockAdapter,
    LocalAdapter,
    OpenAIAdapter,
)
from crewquarters_gateway.config import GatewaySettings
from crewquarters_gateway.control import ACTIVE_RUN_STATES, ControlApiClient
from crewquarters_gateway.credentials import CloudProfile, CredentialProvider
from crewquarters_gateway.idempotency import FinalError
from crewquarters_gateway.manager import ModelManager
from crewquarters_shared import audit, capability
from crewquarters_shared.db.models_gateway import LlmUsage, ModelCatalogEntry
from crewquarters_shared.errors import PlatformError, invalid
from crewquarters_shared.manifest import DEFAULT_VARIANTS

CLOUD_PROVIDERS = ("openai", "anthropic")


@dataclass
class Prepared:
    caller: Caller
    request: ChatRequest
    profile: str
    provider: str
    target: str
    reserved: int
    adapter: Adapter | None
    cloud: CloudProfile | None = None


@dataclass(frozen=True)
class Caller:
    holder_type: str  # run | chat
    holder_id: str
    label: str
    capabilities: frozenset[str] = frozenset()
    model_bindings: dict[str, str] | None = None
    attempt: int | None = None


class InferenceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        manager: ModelManager,
        control: ControlApiClient,
        credentials: CredentialProvider,
        settings: GatewaySettings,
        signing_key: str,
    ) -> None:
        self.sessions = sessions
        self.manager = manager
        self.control = control
        self.credentials = credentials
        self.settings = settings
        self.signing_key = signing_key
        # Tokens reserved by in-flight requests (single gateway process per appliance).
        self._reserved: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._reserved_provider: defaultdict[str, int] = defaultdict(int)

    # --- caller resolution ------------------------------------------------------------

    async def run_caller(self, token: str) -> Caller:
        try:
            claims = capability.verify(token, self.signing_key)
        except jwt.InvalidTokenError as exc:
            raise PlatformError(
                "UNAUTHENTICATED", "The capability token is invalid or expired.", 401
            ) from exc
        run = await self.control.get_run(claims.run_id, max_age=2.0)
        if (
            run is None
            or run["state"] not in ACTIVE_RUN_STATES
            or run["currentAttempt"] != claims.attempt
            or run.get("capabilityTokenId") != claims.token_id
        ):
            raise PlatformError("RUN_NOT_ACTIVE", "This token's run attempt is not active.", 409)
        return Caller(
            holder_type="run",
            holder_id=claims.run_id,
            label=f"Run {claims.run_id[:8]}",
            capabilities=frozenset(claims.capabilities),
            model_bindings=dict(claims.resources.get("modelBindings") or {}),
            attempt=claims.attempt,
        )

    @staticmethod
    def chat_caller(session_id: str, title: str) -> Caller:
        return Caller(holder_type="chat", holder_id=session_id, label=f"Chat: {title[:40]}")

    # --- profile resolution -----------------------------------------------------------

    def _resolve(
        self, caller: Caller, profile: str, cloud: CloudProfile | None = None
    ) -> tuple[str, str]:
        """Return (provider, target) where target is a local model id or a cloud model."""
        provider = profile.split(".", 1)[0]
        if provider == "local":
            variant = profile
            if profile in DEFAULT_VARIANTS:  # family request
                variant = (caller.model_bindings or {}).get(profile) or DEFAULT_VARIANTS[profile]
            if caller.holder_type == "run" and f"llm.profile:{variant}" not in caller.capabilities:
                raise _capability_denied(f"llm.profile:{variant}")
            return "local", variant
        if provider in CLOUD_PROVIDERS:
            if caller.holder_type != "run":
                raise PlatformError("PERMISSION_DENIED", "Chat is local-only.", 403)
            for needed in (f"cloud.{provider}", f"llm.profile:{profile}"):
                if needed not in caller.capabilities:
                    raise _capability_denied(needed)
            return provider, self._cloud_model(provider, profile, cloud)
        raise invalid("UNKNOWN_MODEL_PROFILE", f"Unknown model profile {profile}.")

    def _cloud_model(self, provider: str, profile: str, cloud: CloudProfile | None) -> str:
        """``<provider>.<name>`` -> model id.

        1. Operator override: ``CQ_GATEWAY_<PROVIDER>_MODELS[name]``.
        2. ``default``: the profile's first allowed model, else (Anthropic only)
           ``CQ_GATEWAY_ANTHROPIC_DEFAULT_MODEL``.
        3. A name listed in the profile's ``allowedModels`` is that model.
        A non-empty ``allowedModels`` also restricts the result of 1 and 2.
        """
        name = profile.split(".", 1)[1] if "." in profile else ""
        overrides = (
            self.settings.openai_models if provider == "openai" else self.settings.anthropic_models
        )
        allowed = cloud.allowed_models if cloud is not None else ()
        model = overrides.get(name)
        if model is None and name == "default":
            if allowed:
                model = allowed[0]
            elif provider == "anthropic":
                model = self.settings.anthropic_default_model
        if model is None and name in allowed:
            model = name
        if model is None:
            raise PlatformError(
                "NEEDS_CONFIGURATION",
                f"No model is configured for {profile}. Add it to the {provider} "
                "connection's allowed models.",
                409,
                {"provider": provider, "profile": profile},
            )
        if allowed and model not in allowed:
            raise PlatformError(
                "PERMISSION_DENIED",
                f"{model} is not an allowed model of the {provider} connection.",
                403,
                {"provider": provider, "model": model},
            )
        return model

    # --- budgets and usage ------------------------------------------------------------

    async def _check_budgets(
        self, caller: Caller, provider: str, cloud: CloudProfile | None = None
    ) -> None:
        """Recorded usage plus every in-flight reservation (including this request's).

        Cloud calls also honour the provider profile's ``perRunTokens`` (this run's use
        of the provider) and ``dailyTokens`` (the smaller of it and
        ``CQ_GATEWAY_DAILY_CLOUD_TOKEN_BUDGET`` applies)."""
        async with self.sessions() as db:
            if caller.holder_type == "run":
                used = await db.scalar(
                    select(
                        func.coalesce(func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens), 0)
                    ).where(LlmUsage.holder_type == "run", LlmUsage.holder_id == caller.holder_id)
                )
                pending = self._reserved[("run", caller.holder_id)]
                if int(used or 0) + pending > self.settings.per_run_token_limit:
                    raise PlatformError(
                        "RUN_TOKEN_BUDGET_EXCEEDED", "This run has used its token budget.", 429
                    )
            if provider not in CLOUD_PROVIDERS:
                return
            per_run = cloud.budget("perRunTokens") if cloud is not None else None
            if per_run is not None and caller.holder_type == "run":
                used = await db.scalar(
                    select(
                        func.coalesce(func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens), 0)
                    ).where(
                        LlmUsage.holder_type == "run",
                        LlmUsage.holder_id == caller.holder_id,
                        LlmUsage.provider == provider,
                    )
                )
                if int(used or 0) + self._reserved[("run", caller.holder_id)] > per_run:
                    raise PlatformError(
                        "RUN_TOKEN_BUDGET_EXCEEDED",
                        f"This run has used its {provider} token budget.",
                        429,
                        {"provider": provider, "limit": "perRunTokens"},
                    )
            limits = [
                v
                for v in (
                    self.settings.daily_cloud_token_budget,
                    cloud.budget("dailyTokens") if cloud is not None else None,
                )
                if v is not None and v > 0
            ]
            if limits:
                used = await db.scalar(
                    select(
                        func.coalesce(func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens), 0)
                    ).where(LlmUsage.provider == provider, LlmUsage.day == date.today())
                )
                if int(used or 0) + self._reserved_provider[provider] > min(limits):
                    raise PlatformError(
                        "CLOUD_BUDGET_EXCEEDED",
                        f"Today's {provider} token budget is used up.",
                        429,
                        {"provider": provider, "limit": "dailyTokens"},
                    )

    async def _record(
        self,
        caller: Caller,
        result: ChatResult | None,
        provider: str,
        model: str,
        outcome: str,
        latency_ms: int,
    ) -> None:
        async with self.sessions() as db, db.begin():
            db.add(
                LlmUsage(
                    provider=provider,
                    model=model,
                    holder_type=caller.holder_type,
                    holder_id=caller.holder_id,
                    input_tokens=result.input_tokens if result else 0,
                    output_tokens=result.output_tokens if result else 0,
                    latency_ms=latency_ms,
                    outcome=outcome,
                    request_id=result.request_id if result else None,
                )
            )
            if provider in CLOUD_PROVIDERS:  # cloud use is always audited; prompts never are
                audit.record(
                    db,
                    action="llm.cloud_call",
                    actor_type="service",
                    actor_id=f"{caller.holder_type}:{caller.holder_id}",
                    target_type="provider",
                    target_id=provider,
                    outcome="success" if outcome == "ok" else "failure",
                    metadata={
                        "model": model,
                        "outcome": outcome,
                        "requestId": result.request_id if result else None,
                    },
                )

    # --- adapters -----------------------------------------------------------------------

    async def _local_adapter(self, caller: Caller, model_id: str) -> Adapter:
        async with self.sessions() as db:
            if await db.get(ModelCatalogEntry, model_id) is None:
                raise invalid("UNKNOWN_MODEL_PROFILE", f"{model_id} is not in the model catalog.")
        ttl = (
            self.settings.run_lease_ttl_seconds
            if caller.holder_type == "run"
            else self.settings.chat_lease_ttl_seconds
        )
        await self.manager.acquire(
            model_id, caller.holder_type, caller.holder_id, caller.label, ttl
        )
        async with self.sessions() as db:
            instance_state = (await self.manager.describe(db, model_id))["memoryState"]
        signalled = False
        if instance_state != "READY" and caller.holder_type == "run" and caller.attempt is not None:
            await self.control.set_model_loading(caller.holder_id, caller.attempt, True, model_id)
            signalled = True
        try:
            endpoint = await self.manager.wait_ready(model_id)
        finally:
            if signalled:
                await self.control.set_model_loading(
                    caller.holder_id, caller.attempt or 0, False, model_id
                )
        if endpoint.base_url is None:
            return InProcessMockAdapter(endpoint.served_model)
        return LocalAdapter(
            endpoint.base_url, endpoint.served_model, self.settings.request_timeout_seconds
        )

    async def _cloud_adapter(
        self, provider: str, model: str, profile: CloudProfile | None = None
    ) -> Adapter:
        key = await self.credentials.api_key(provider, profile)
        if not key:
            reason = getattr(self.credentials, "reason", None)
            raise PlatformError(
                "NEEDS_CONNECTION",
                reason or f"No usable {provider} key is configured. Add one in Connections.",
                409,
                {"provider": provider},
            )
        return self.adapter_for(provider, key, model, self.settings.request_timeout_seconds)

    def adapter_for(
        self, provider: str, key: str, model: str, timeout: float, *, max_retries: int = 2
    ) -> OpenAIAdapter | AnthropicAdapter:
        """Build a cloud adapter (tests replace this to inject mocked transports)."""
        if provider == "openai":
            return OpenAIAdapter(key, model, timeout)
        return AnthropicAdapter(
            key,
            model,
            timeout,
            fallbacks=self.settings.anthropic_fallbacks,
            max_retries=max_retries,
        )

    def _request(self, body: dict[str, Any]) -> ChatRequest:
        messages = body.get("messages") or []
        if not messages or not all(
            isinstance(m, dict)
            and m.get("role") in ("system", "user", "assistant")
            and isinstance(m.get("content"), str)
            for m in messages
        ):
            raise invalid(
                "INVALID_MESSAGES", "messages must be {role, content} objects with string content."
            )
        max_tokens = int(body.get("maxOutputTokens") or 1000)
        if not 1 <= max_tokens <= self.settings.max_output_tokens:
            raise invalid(
                "MAX_OUTPUT_TOKENS", f"maxOutputTokens must be 1-{self.settings.max_output_tokens}."
            )
        return ChatRequest(
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            max_output_tokens=max_tokens,
            temperature=body.get("temperature"),
            response_schema=body.get("responseSchema"),
            tools=list(body.get("tools") or []),
        )

    def _estimate(self, request: ChatRequest) -> int:
        """Worst-case tokens for budget reservation: prompt (~4 chars/token) + output cap."""
        return sum(len(m["content"]) for m in request.messages) // 4 + request.max_output_tokens

    def _reserve(self, caller: Caller, provider: str, tokens: int) -> None:
        self._reserved[(caller.holder_type, caller.holder_id)] += tokens
        self._reserved_provider[provider] += tokens

    def _unreserve(self, prepared: Prepared) -> None:
        self._reserved[(prepared.caller.holder_type, prepared.caller.holder_id)] -= (
            prepared.reserved
        )
        self._reserved_provider[prepared.provider] -= prepared.reserved

    async def prepare(self, caller: Caller, body: dict[str, Any]) -> Prepared:
        """Validate, authorize, reserve budget, and obtain an adapter (loading the model
        if needed). Runs before any response starts, so failures are proper HTTP errors."""
        profile = str(body.get("profile", ""))
        request = self._request(body)
        cloud = None
        if caller.holder_type == "run" and profile.split(".", 1)[0] in CLOUD_PROVIDERS:
            cloud = await self.credentials.profile(profile.split(".", 1)[0])
        provider, target = self._resolve(caller, profile, cloud)
        estimate = self._estimate(request)
        # Reserve before the (awaiting) budget check so parallel requests see each other.
        self._reserve(caller, provider, estimate)
        prepared = Prepared(caller, request, profile, provider, target, estimate, None, cloud)
        try:
            await self._check_budgets(caller, provider, cloud)
            prepared.adapter = await (
                self._local_adapter(caller, target)
                if provider == "local"
                else self._cloud_adapter(provider, target, cloud)
            )
        except BaseException:
            self._unreserve(prepared)
            raise
        return prepared

    async def _finish(self, prepared: Prepared, result: ChatResult) -> dict[str, Any]:
        """Record usage first (the provider has billed it), then enforce the outcome."""
        outcome = (
            "refused"
            if result.finish_reason == "refusal"
            else ("invalid_output" if result.structured_error else "ok")
        )
        await self._record(
            prepared.caller, result, prepared.provider, result.model, outcome, result.latency_ms
        )
        # FinalError: the provider produced (and billed) this outcome, so an idempotent
        # retry replays it instead of calling the provider again.
        if result.finish_reason == "refusal":
            raise FinalError(
                "MODEL_REFUSED",
                "The model declined this request.",
                422,
                {"category": result.refusal_category, "provider": prepared.provider},
            )
        if result.structured_error:
            raise FinalError(
                "STRUCTURED_OUTPUT_INVALID",
                result.structured_error,
                502,
                {"provider": prepared.provider},
            )
        return result.to_wire(prepared.profile)

    # --- entry points -----------------------------------------------------------------

    async def chat(self, caller: Caller, body: dict[str, Any]) -> dict[str, Any]:
        prepared = await self.prepare(caller, body)
        assert prepared.adapter is not None
        started = time.perf_counter()
        self.manager.inflight[prepared.target] += 1
        try:
            result = await prepared.adapter.chat(prepared.request)
        except PlatformError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            await self._record(caller, None, prepared.provider, prepared.target, exc.code, elapsed)
            raise
        finally:
            self.manager.inflight[prepared.target] -= 1
            self._unreserve(prepared)
        return await self._finish(prepared, result)

    async def stream(self, prepared: Prepared) -> AsyncIterator[dict[str, Any]]:
        assert prepared.adapter is not None
        started = time.perf_counter()
        self.manager.inflight[prepared.target] += 1
        streamed: list[str] = []
        try:
            async for event in prepared.adapter.stream(prepared.request):
                if event["type"] == "result":
                    try:
                        response = await self._finish(prepared, event["result"])
                    except PlatformError as exc:
                        yield {
                            "type": "error",
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                "details": exc.details,
                            },
                        }
                        return
                    yield {"type": "done", "response": response}
                else:
                    streamed.append(event.get("text", ""))
                    yield event
        except PlatformError as exc:
            await self._record_partial(prepared, streamed, exc.code, started)
            yield {
                "type": "error",
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            }
        except (asyncio.CancelledError, GeneratorExit):
            with contextlib.suppress(Exception):
                await self._record_partial(prepared, streamed, "cancelled", started)
            raise
        finally:
            self.manager.inflight[prepared.target] -= 1
            self._unreserve(prepared)

    async def _record_partial(
        self, prepared: Prepared, streamed: list[str], outcome: str, started: float
    ) -> None:
        """A stream that ended early was still billed for what it produced: estimate it."""
        estimate = ChatResult(
            text="",
            finish_reason=outcome,
            provider=prepared.provider,
            model=prepared.target,
            input_tokens=sum(len(m["content"]) for m in prepared.request.messages) // 4,
            output_tokens=max(len("".join(streamed)) // 4, 1 if streamed else 0),
        )
        await self._record(
            prepared.caller,
            estimate,
            prepared.provider,
            prepared.target,
            outcome,
            int((time.perf_counter() - started) * 1000),
        )


def _capability_denied(capability: str) -> PlatformError:
    return PlatformError(
        "CAPABILITY_DENIED",
        f"This run is not allowed to use {capability}.",
        403,
        {"capability": capability},
    )
