"""The conversation layer in text mode: LiveKit's AgentSession with the real OpenAI plugin,
talking to the fake platform's OpenAI-compatible facade (no audio, no LiveKit server)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from livekit.agents import AgentSession
from livekit.plugins import openai
from voice_helpers import make_config

from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.contracts import load_manifest
from crewquarters_fake.llm_rules import RuleSet
from crewquarters_fake.server import BackgroundServer
from crewquarters_fake.settings import FakeSettings
from voice_caller.prompts import build_system_prompt
from voice_caller.session import VoiceCallAgent, plain_speech

AGENT_DIR = Path(__file__).resolve().parents[1]
CONFIG = {"spreadsheetId": "s", "organization": "Acme Dental", "purpose": "confirm appointments"}


@pytest.fixture
def platform() -> Iterator[tuple[BackgroundServer, Any]]:
    app = create_app(FakeSettings(heartbeat_seconds=0.2, record_traffic=True))
    with BackgroundServer(app) as server:
        yield server, app.state.store


def endpoint_for(server: BackgroundServer) -> tuple[str, str]:
    """A running voice-agent run's OpenAI-compatible endpoint (base URL, run token)."""
    client = FakePlatformClient(server.url)
    manifest = load_manifest(AGENT_DIR / "manifest.yaml")
    client.register_manifest(manifest)
    installation = client.install(
        "voice-call-center", "0.1.0", CONFIG, manifest["spec"]["permissions"]
    )
    run = client.create_run(installation["id"])
    token = client.dispatch(run["id"], server.url)["env"]["PLATFORM_RUN_TOKEN"]
    sdk = f"{server.url}/internal/v1/sdk"
    handshake = {"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "voice-call-center"}
    httpx.post(f"{sdk}/handshake", json=handshake, headers={"Authorization": f"Bearer {token}"})
    return f"{sdk}/openai/v1", token


def llm_for(server: BackgroundServer) -> Any:
    base_url, token = endpoint_for(server)
    return openai.LLM(model="local.general.small", base_url=base_url, api_key=token)


async def test_not_interested_ends_the_call_with_the_tool(platform: Any) -> None:
    server, store = platform
    store.gateway.rules = RuleSet.from_list(
        [
            {
                "name": "no",
                "match": {"lastUser": True, "contains": ["not interested"]},
                "respond": {
                    "text": "No problem, thanks for your time.",
                    "toolCall": {"name": "end_call", "arguments": {}},
                },
            }
        ]
    )
    hung_up = asyncio.Event()

    async def hang_up() -> None:
        hung_up.set()

    agent = VoiceCallAgent(build_system_prompt(make_config(), "Asha"), hang_up)
    async with AgentSession(llm=llm_for(server)) as session:
        await session.start(agent)
        result = await session.run(user_input="Sorry, I'm not interested.")
        result.expect.contains_message(role="assistant")
        result.expect.contains_function_call(name="end_call")
    assert hung_up.is_set()


async def test_the_model_is_given_the_honest_prompt(platform: Any) -> None:
    server, store = platform
    store.gateway.rules = RuleSet.from_list(
        [
            {
                "name": "any",
                "match": {"lastUser": True},
                "respond": {"text": "I'm an automated assistant calling for Acme Dental."},
            }
        ]
    )
    agent = VoiceCallAgent(build_system_prompt(make_config(), "Asha"), lambda: asyncio.sleep(0))
    async with AgentSession(llm=llm_for(server)) as session:
        await session.start(agent)
        result = await session.run(user_input="Wait, are you a robot?")
        result.expect.contains_message(role="assistant")
    chats = [t for t in store.traffic if t["path"].endswith("/openai/v1/chat/completions")]
    system = chats[-1]["requestBody"]["messages"][0]
    assert system["role"] in {"system", "developer"}
    assert "never claim to be one" in str(system["content"])
    assert any(t["requestBody"].get("tools") for t in chats)  # end_call is offered


async def test_filter_words_are_masked_before_the_model_sees_them() -> None:
    """LiveKit calls this hook on the audio path before the reply is generated (text-mode
    ``session.run`` skips it), so it is exercised directly."""
    from livekit.agents.llm import ChatContext, ChatMessage

    agent = VoiceCallAgent(build_system_prompt(make_config(), "Asha"), lambda: asyncio.sleep(0))
    message = ChatMessage(role="user", content=["This is bullshit, why are you calling"])
    await agent.on_user_turn_completed(ChatContext.empty(), message)
    assert "bullshit" not in (message.text_content or "")
    assert agent.raw_user_text[message.id] == "This is bullshit, why are you calling"


async def test_plain_speech_drops_markup_across_chunks() -> None:
    async def chunks() -> Any:
        for piece in ["[warm and ", "lively] Hi, <break time=", '"300ms" />there', " [sigh]."]:
            yield piece

    spoken = "".join([piece async for piece in plain_speech(chunks())])
    assert spoken == " Hi, there ."
