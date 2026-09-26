"""The broker's OpenAI-compatible model facade (voice agents use it through LiveKit's plugins)."""

from __future__ import annotations

import io
import json
import wave
from typing import Any

import httpx
from fake_helpers import SDK, audit, manifest, start
from fastapi import FastAPI

from crewquarters_fake.llm_rules import RuleSet

OPENAI = f"{SDK}/openai/v1"
VOICE_PROFILES = ["local.general", "local.stt", "local.tts"]


def voice_manifest() -> dict[str, Any]:
    return manifest(llmProfiles=VOICE_PROFILES, userInput=True)


def wav(seconds: float = 0.4, rate: int = 48000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return out.getvalue()


def sse_chunks(text: str) -> list[dict[str, Any]]:
    lines = [line.removeprefix("data: ") for line in text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


def chat(**extra: Any) -> dict[str, Any]:
    return {
        "model": "local.general.small",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hello"},
        ],
        **extra,
    }


async def test_chat_completion_json(app: FastAPI, api: httpx.AsyncClient) -> None:
    app.state.store.gateway.rules = RuleSet.from_list(
        [{"name": "hi", "match": {"contains": ["hello"]}, "respond": {"text": "Hi there!"}}]
    )
    started = await start(api, voice_manifest())
    r = await api.post(f"{OPENAI}/chat/completions", json=chat(), headers=started.headers)
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hi there!"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["completion_tokens"] > 0
    [usage] = [a for a in await audit(api) if a["action"] == "llm.call"]
    assert usage["profile"] == "local.general.small" and "messages" not in usage


async def test_chat_completion_stream_yields_deltas_then_usage(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    app.state.store.gateway.rules = RuleSet.from_list(
        [
            {
                "name": "hi",
                "match": {"contains": ["hello"]},
                "respond": {"text": "Hi there, how are you today?"},
            }
        ]
    )
    started = await start(api, voice_manifest())
    r = await api.post(
        f"{OPENAI}/chat/completions",
        json=chat(stream=True, stream_options={"include_usage": True}),
        headers=started.headers,
    )
    assert r.headers["content-type"].startswith("text/event-stream")
    chunks = sse_chunks(r.text)
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert text == "Hi there, how are you today?"
    finishes = [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]]
    assert finishes[-1] == "stop"
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["total_tokens"] > 0


async def test_rule_can_answer_with_a_tool_call(app: FastAPI, api: httpx.AsyncClient) -> None:
    app.state.store.gateway.rules = RuleSet.from_list(
        [
            {
                "name": "bye",
                "match": {"lastUser": True, "contains": ["not interested"]},
                "respond": {
                    "text": "No problem, thanks for your time.",
                    "toolCall": {"name": "end_call", "arguments": {}},
                },
            }
        ]
    )
    started = await start(api, voice_manifest())
    messages = [
        {"role": "assistant", "content": "Hi, do you have a minute?"},
        {"role": "user", "content": "I'm not interested."},
    ]
    tools = [
        {
            "type": "function",
            "function": {"name": "end_call", "parameters": {"type": "object", "properties": {}}},
        }
    ]
    r = await api.post(
        f"{OPENAI}/chat/completions",
        json={"model": "local.general.small", "messages": messages, "tools": tools, "stream": True},
        headers=started.headers,
    )
    chunks = sse_chunks(r.text)
    calls = [
        tc
        for c in chunks
        if c["choices"]
        for tc in c["choices"][0]["delta"].get("tool_calls") or []
    ]
    assert calls[0]["function"]["name"] == "end_call"
    assert json.loads(calls[0]["function"]["arguments"]) == {}
    assert calls[0]["id"] and calls[0]["index"] == 0
    assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]][-1] == "tool_calls"


async def test_last_user_rules_ignore_earlier_turns(app: FastAPI, api: httpx.AsyncClient) -> None:
    app.state.store.gateway.rules = RuleSet.from_list(
        [
            {
                "name": "a",
                "match": {"lastUser": True, "contains": ["hello"]},
                "respond": {"text": "first"},
            },
            {
                "name": "b",
                "match": {"lastUser": True, "contains": ["tuesday"]},
                "respond": {"text": "second"},
            },
        ]
    )
    started = await start(api, voice_manifest())
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": [{"type": "text", "text": "Tuesday works"}]},
    ]
    r = await api.post(
        f"{OPENAI}/chat/completions",
        json={"model": "local.general.small", "messages": messages},
        headers=started.headers,
    )
    assert r.json()["choices"][0]["message"]["content"] == "second"


async def test_ungranted_model_is_denied_and_audited(api: httpx.AsyncClient) -> None:
    started = await start(api, voice_manifest())
    r = await api.post(
        f"{OPENAI}/chat/completions",
        json=chat(model="local.general.quality"),
        headers=started.headers,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CAPABILITY_DENIED"
    assert any(a["action"] == "capability.denied" for a in await audit(api))


async def test_transcription_uses_the_speech_service(app: FastAPI, api: httpx.AsyncClient) -> None:
    started = await start(api, voice_manifest())
    app.state.store.fake_speech.queue_transcripts(["Yes, I have a minute."])
    r = await api.post(
        f"{OPENAI}/audio/transcriptions",
        data={"model": "local.stt.small", "language": "en"},
        files={"file": ("u.wav", wav(), "audio/wav")},
        headers=started.headers,
    )
    assert r.json() == {"text": "Yes, I have a minute."}
    [usage] = [a for a in await audit(api) if a["action"] == "speech.call"]
    assert usage["operation"] == "transcribe" and usage["profile"] == "local.stt.small"
    assert "text" not in usage


async def test_speech_streams_audio(app: FastAPI, api: httpx.AsyncClient) -> None:
    started = await start(api, voice_manifest())
    r = await api.post(
        f"{OPENAI}/audio/speech",
        json={
            "model": "local.tts.small",
            "input": "Hi, this is Sam.",
            "voice": "af_sarah",
            "response_format": "pcm",
        },
        headers=started.headers,
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/pcm")
    assert len(r.content) > 24000  # at least half a second of 16-bit 24 kHz audio
    assert app.state.store.fake_speech.synthesized == ["Hi, this is Sam."]


async def test_speech_profiles_must_be_granted(api: httpx.AsyncClient) -> None:
    started = await start(api, manifest(llmProfiles=["local.general"], userInput=True))
    r = await api.post(
        f"{OPENAI}/audio/speech",
        json={"model": "local.tts.small", "input": "Hi."},
        headers=started.headers,
    )
    assert r.status_code == 403


async def test_chat_model_cannot_be_used_for_speech(api: httpx.AsyncClient) -> None:
    started = await start(api, voice_manifest())
    r = await api.post(
        f"{OPENAI}/audio/speech",
        json={"model": "local.general.small", "input": "Hi."},
        headers=started.headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "WRONG_MODEL_KIND"


async def test_admin_queues_and_reports_fake_speech(api: httpx.AsyncClient) -> None:
    started = await start(api, voice_manifest())
    await api.post("/fake/v1/speech/transcripts", json={"lines": ["Who is this?"]})
    r = await api.post(
        f"{OPENAI}/audio/transcriptions",
        data={"model": "local.stt.small"},
        files={"file": ("u.wav", wav(), "audio/wav")},
        headers=started.headers,
    )
    assert r.json()["text"] == "Who is this?"
    state = (await api.get("/fake/v1/state/speech")).json()
    assert state["transcribed"] == ["Who is this?"]
