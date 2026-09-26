"""Speech-to-text models: whole-file transcription through a short manual lease, the
capability split between chat and transcription models, and admission beside a chat
model."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import httpx
from gateway_helpers import CHAT_TOKEN, QUALITY, SMALL, TOKEN, chat_body, install
from sqlalchemy import select

from crewquarters_gateway.adapters import mock_transcript
from crewquarters_gateway.config import GiB
from crewquarters_gateway.main import Gateway
from crewquarters_shared.db.models_gateway import LlmUsage, ModelInstance, ModelLease

ASR = "local.asr.r2t2"
URL = "/internal/v1/audio/transcriptions"
AUDIO = b"RIFF" + bytes(2048)
MOCK_SERVER = Path(__file__).resolve().parents[3] / "catalog/models/dev/files/mock_openai_server.py"


async def transcribe(
    client: httpx.AsyncClient, model: str = ASR, audio: bytes = AUDIO, **params: str
) -> httpx.Response:
    return await client.post(
        URL,
        params={"modelId": model, "filename": "memo.wav", **params},
        content=audio,
        headers={"Content-Type": "audio/wav", "X-Actor-Id": "owner-1"},
    )


async def test_catalog_marks_the_asr_model_as_transcription_only(
    gw_client: httpx.AsyncClient,
) -> None:
    model = (await gw_client.get(f"/internal/v1/models/{ASR}")).json()
    assert model["capabilities"] == ["transcription"] and model["family"] == "local.asr"


async def test_transcription_loads_on_demand_and_releases_its_lease(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    not_installed = await transcribe(gw_client)
    assert not_installed.status_code == 409
    assert not_installed.json()["error"]["code"] == "MODEL_NOT_INSTALLED"
    await install(gw_client, ASR)

    response = await transcribe(gw_client, language="en")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == mock_transcript("memo.wav", len(AUDIO))
    assert body["modelId"] == ASR and body["language"] == "en" and body["requestId"]

    async with gateway.sessions() as db:
        instance = await db.get(ModelInstance, ASR)
        assert instance is not None and instance.state == "READY"
        assert instance.idle_since is not None  # no lease left: the idle timer governs
        leases = (await db.scalars(select(ModelLease).where(ModelLease.model_id == ASR))).all()
        assert len(leases) == 1 and leases[0].holder_type == "manual"
        assert leases[0].released_at is not None
        usage = (await db.scalars(select(LlmUsage).where(LlmUsage.model == ASR))).all()
        assert [(u.provider, u.holder_type, u.outcome) for u in usage] == [
            ("local", "manual", "ok")
        ]


async def test_chat_and_transcription_models_are_not_interchangeable(
    gw_client: httpx.AsyncClient,
) -> None:
    await install(gw_client, ASR)
    await install(gw_client)
    chat = await gw_client.post("/internal/v1/llm/chat", json=chat_body(profile=ASR))
    assert chat.status_code == 422
    assert chat.json()["error"]["code"] == "MODEL_CAPABILITY_UNSUPPORTED"
    assert chat.json()["error"]["details"] == {"modelId": ASR, "capability": "chat"}
    wrong = await transcribe(gw_client, SMALL)
    assert wrong.status_code == 422
    assert wrong.json()["error"]["details"]["capability"] == "transcription"
    unknown = await transcribe(gw_client, "local.asr.nope")
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "UNKNOWN_MODEL_PROFILE"


async def test_asr_model_loads_beside_a_chat_model_but_memory_limits_still_apply(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    await install(gw_client, QUALITY)
    await install(gw_client, ASR)
    assert (await gw_client.post(f"/internal/v1/models/{SMALL}/load")).status_code == 202
    assert (await transcribe(gw_client)).status_code == 200
    # The one-generative-model policy still blocks a second chat model.
    blocked = await gw_client.post(f"/internal/v1/models/{QUALITY}/load")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["loadedModels"] == [SMALL]

    await gw_client.post(f"/internal/v1/models/{ASR}/unload", json={"force": True})
    for _ in range(200):
        state = (await gw_client.get(f"/internal/v1/models/{ASR}")).json()["memoryState"]
        if state == "NOT_LOADED":
            break
        await asyncio.sleep(0.05)
    gateway.settings.max_serving_bytes = 4 * GiB  # small (2) + asr (2) + margin (1) > 4
    over = await transcribe(gw_client)
    assert over.status_code == 409 and "maxServingBytes" in over.json()["error"]["details"]


async def test_transcription_request_validation(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client, ASR)
    empty = await transcribe(gw_client, audio=b"")
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "EMPTY_AUDIO"
    language = await transcribe(gw_client, language="english")
    assert language.status_code == 422
    assert language.json()["error"]["code"] == "INVALID_LANGUAGE"


async def test_transcription_rejects_oversized_audio(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client, ASR)
    gateway.settings.max_audio_bytes = 1024
    response = await transcribe(gw_client)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_transcription_is_reserved_for_the_control_api(
    gw_client: httpx.AsyncClient,
) -> None:
    await install(gw_client, ASR)
    service_only = {"Authorization": f"Bearer {TOKEN}", "X-Chat-Client-Token": ""}
    response = await gw_client.post(
        URL, params={"modelId": ASR}, content=AUDIO, headers=service_only
    )
    assert response.status_code == 401
    wrong = {"Authorization": "Bearer nope", "X-Chat-Client-Token": CHAT_TOKEN}
    response = await gw_client.post(URL, params={"modelId": ASR}, content=AUDIO, headers=wrong)
    assert response.status_code == 401


def test_mock_server_transcript_matches_the_in_process_mock() -> None:
    spec = importlib.util.spec_from_file_location("mock_openai_server", MOCK_SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, size in (("memo.wav", 10), (None, 0)):
        assert module.transcript_for(name, size) == mock_transcript(name, size)
