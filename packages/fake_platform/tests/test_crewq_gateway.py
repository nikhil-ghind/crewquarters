"""The fake platform's model facade can use a real Crewquarters model gateway (the appliance's
models) with the broker's voice credential: chat, speech-to-text, and speech."""

from __future__ import annotations

import io
import json
import wave

import httpx
import numpy as np

from crewquarters_fake.crewq_gateway import CrewquartersGateway

SERVICE, VOICE = "service-token", "voice-token"


def wav(rate: int, seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(np.zeros(int(rate * seconds), dtype="<i2").tobytes())
    return buffer.getvalue()


class StubGateway:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {SERVICE}"
        assert request.headers["x-voice-client-token"] == VOICE
        path = request.url.path
        if path == "/internal/v1/llm/chat":
            events = [
                {"type": "delta", "text": "Hello "},
                {"type": "delta", "text": "there."},
                {"type": "done", "response": {"usage": {"inputTokens": 7, "outputTokens": 2}}},
            ]
            body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        if path == "/internal/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": " Yes, I have a minute. "})
        if path == "/internal/v1/audio/speech":
            return httpx.Response(
                200, content=wav(16000, 0.5), headers={"content-type": "audio/wav"}
            )
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": path}})


def gateway(stub: StubGateway) -> CrewquartersGateway:
    return CrewquartersGateway(
        "http://gateway:8090",
        SERVICE,
        VOICE,
        stt_model="local.asr.r2t2",
        tts_model="local.tts.voxtream",
        transport=httpx.MockTransport(stub),
    )


async def test_chat_goes_to_the_gateway_as_a_voice_holder_with_plain_messages() -> None:
    stub = StubGateway()
    body = {
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "content": "ok", "tool_call_id": "c1"},
        ],
        "tools": [{"type": "function", "function": {"name": "end_call"}}],
        "max_completion_tokens": 80,
        "temperature": 0.4,
    }
    reply = await gateway(stub).chat("local.general.small", body)
    assert reply.text == "Hello there." and reply.tool_calls == []
    assert (reply.input_tokens, reply.output_tokens) == (7, 2)
    sent = json.loads(stub.requests[0].content)
    assert sent["profile"] == "local.general.small" and sent["stream"] is True
    assert sent["holder"]["type"] == "voice" and sent["holder"]["id"]
    assert sent["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Hi"},
    ]
    assert sent["maxOutputTokens"] == 80 and sent["temperature"] == 0.4
    assert "tools" not in sent  # the gateway's chat route takes no tools (D13)


async def test_transcription_uses_the_configured_speech_to_text_model() -> None:
    stub = StubGateway()
    text = await gateway(stub).transcribe(wav(16000, 0.2), "local.stt.small", "en")
    assert text == "Yes, I have a minute."
    request = stub.requests[0]
    assert request.url.params["modelId"] == "local.asr.r2t2"
    assert request.url.params["holderId"] and request.url.params["language"] == "en"


async def test_speech_is_returned_as_24_khz_pcm() -> None:
    stub = StubGateway()
    chunks = [
        c async for c in gateway(stub).synthesize("local.tts.small", "Hello.", "af_sarah", "pcm", 1)
    ]
    audio = b"".join(chunks)
    assert len(audio) == 2 * 24000 // 2  # 0.5 s of 16-bit samples at 24 kHz
    sent = json.loads(stub.requests[0].content)
    assert sent["modelId"] == "local.tts.voxtream" and sent["input"] == "Hello."
