"""Shared fixtures and helpers for the model-gateway tests (registered as a pytest plugin
in the root conftest; importable as ``gateway_helpers``)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from crewquarters_gateway.adapters import AnthropicAdapter, OpenAIAdapter
from crewquarters_gateway.config import GatewaySettings, GiB
from crewquarters_gateway.main import Gateway, create_app
from crewquarters_gateway.runtime import InProcessModelRuntime
from crewquarters_shared import capability

ROOT = Path(__file__).resolve().parents[3]
TOKEN = "dev-insecure-internal-token-change-me-0000"
SIGNING = "dev-insecure-capability-key-change-me-00000"
CHAT_TOKEN = "dev-insecure-chat-token-change-me-000000000"
SERVICE = {"Authorization": f"Bearer {TOKEN}", "X-Chat-Client-Token": CHAT_TOKEN}
SMALL, QUALITY = "local.general.small", "local.general.quality"


class FakeControl:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.signals: list[tuple[str, bool]] = []

    async def get_run(self, run_id: str, max_age: float = 0.0) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def run_is_active(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        return bool(run) and run["state"] in {
            "RUNNING",
            "LOADING_MODEL",
            "WAITING_INPUT",
            "PREPARING",
        }

    async def set_model_loading(
        self, run_id: str, attempt: int, loading: bool, model: str | None
    ) -> None:
        self.signals.append((run_id, loading))

    async def close(self) -> None:
        return None


def gateway_settings(**overrides: Any) -> GatewaySettings:
    base = {
        "catalog_dir": ROOT / "catalog/models/dev",
        "runtime": "inprocess",
        "system_reserve_bytes": 2 * GiB,
        "max_serving_bytes": 16 * GiB,
        "load_safety_margin_bytes": 1 * GiB,
        "idle_unload_seconds": 3600,
        "load_poll_seconds": 0.05,
        "wait_ready_seconds": 10,
        "manual_drain_seconds": 1,
    }
    base.update(overrides)
    return GatewaySettings(**base)


@pytest.fixture
async def gateway(settings) -> AsyncIterator[Gateway]:  # type: ignore[no-untyped-def]
    gw = Gateway(
        settings, gateway_settings(), runtime=InProcessModelRuntime(), control=FakeControl()
    )  # type: ignore[arg-type]
    await gw.sync_catalog()
    stop = asyncio.Event()
    worker = asyncio.create_task(gw.worker_loop(stop))
    try:
        yield gw
    finally:
        stop.set()
        await worker
        await gw.close()


@pytest.fixture
async def gw_client(gateway: Gateway) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(gateway, background=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway", headers=SERVICE
    ) as c:
        yield c


async def install(gw_client: httpx.AsyncClient, model: str = SMALL) -> None:
    response = await gw_client.post(f"/internal/v1/models/{model}/install")
    assert response.status_code == 202, response.text
    assert response.json()["downloadState"] == "INSTALLED"


def chat_body(**extra: Any) -> dict[str, Any]:
    return {
        "profile": SMALL,
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hello crew"},
        ],
        "maxOutputTokens": 50,
        "holder": {"type": "chat", "id": "session-1", "label": "My chat"},
        **extra,
    }


def run_token(
    gateway: Gateway, run_id: str, attempt: int = 1, caps: list[str] | None = None
) -> tuple[str, str]:
    token, claims = capability.mint(
        signing_key=SIGNING,
        run_id=uuid.UUID(run_id),
        attempt=attempt,
        installation_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        capabilities=caps if caps is not None else [f"llm.profile:{SMALL}"],
        resources={"modelBindings": {"local.general": SMALL}},
        ttl_seconds=600,
    )
    control: FakeControl = gateway.control  # type: ignore[assignment]
    control.runs[run_id] = {
        "state": "RUNNING",
        "currentAttempt": attempt,
        "capabilityTokenId": claims.token_id,
    }
    return token, claims.token_id


def anthropic_client(handler: Any, api_key: str = "sk-test") -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def mock_cloud(gateway: Gateway, *, anthropic: Any = None, openai: Any = None) -> list[str]:
    """Route the gateway's cloud adapters to mocked transports. ``anthropic`` takes an
    ``httpx2`` handler (the SDK's client), ``openai`` an ``httpx`` handler. Returns the
    API keys the adapters were built with, for assertions."""
    keys: list[str] = []
    original = gateway.inference.adapter_for

    def adapter_for(
        provider: str, key: str, model: str, timeout: float, *, max_retries: int = 2
    ) -> OpenAIAdapter | AnthropicAdapter:
        keys.append(key)
        adapter = original(provider, key, model, timeout, max_retries=max_retries)
        if isinstance(adapter, AnthropicAdapter):
            assert anthropic is not None, "unexpected Anthropic call"
            adapter.client = anthropic_client(anthropic, key)
        else:
            assert openai is not None, "unexpected OpenAI call"
            adapter.transport = httpx.MockTransport(openai)
        return adapter

    gateway.inference.adapter_for = adapter_for  # type: ignore[method-assign]
    return keys


@pytest.fixture(autouse=True)
def _no_real_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach real providers: unmocked clients hit a closed local port."""
    from crewquarters_gateway.adapters import OpenAIAdapter

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(OpenAIAdapter, "base_url", "http://127.0.0.1:9/v1")


ASR_TTS_INSTALL = ("local.asr.r2t2", SMALL, "local.tts.voxtream")


async def install_models(gateway: Gateway, models: tuple[str, ...]) -> None:
    """Install models straight through the manager (the in-process runtime is instant)."""
    for model in models:
        state = await gateway.manager.install(model)
        assert state["downloadState"] == "INSTALLED", state
