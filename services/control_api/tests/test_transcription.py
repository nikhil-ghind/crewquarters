"""Owner transcription through the control API and the real model gateway (in-process
mock runtime): upload -> gateway lease/load -> mock speech-to-text model -> transcript."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from gateway_helpers import CHAT_TOKEN, TOKEN, install
from sqlalchemy import select

from crewquarters_api.gateway_client import GatewayClient
from crewquarters_api.main import create_app
from crewquarters_gateway.adapters import mock_transcript
from crewquarters_gateway.main import Gateway
from crewquarters_gateway.main import create_app as create_gateway_app
from crewquarters_shared.config import Settings
from crewquarters_shared.db.models import AuditEvent

ASR = "local.asr.r2t2"
AUDIO = b"RIFF" + bytes(4096)


@pytest.fixture
async def gw_app(gateway: Gateway) -> Any:
    return create_gateway_app(gateway, background=False)


@pytest.fixture
async def app(settings: Settings, gw_app: Any) -> AsyncIterator[Any]:
    """Overrides the root ``app``: model calls go to the real gateway app."""
    application = create_app(settings)
    application.state.cq.models = GatewayClient(
        "http://gateway", TOKEN, transport=httpx.ASGITransport(app=gw_app), chat_token=CHAT_TOKEN
    )
    yield application
    await application.state.engine.dispose()


@pytest.fixture
async def gw(gw_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Chat-Client-Token": CHAT_TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw_app), base_url="http://gateway", headers=headers
    ) as client:
        yield client


def _upload(name: str = "memo.wav", audio: bytes = AUDIO) -> dict[str, Any]:
    return {"file": (name, audio, "application/octet-stream")}


async def test_owner_transcribes_an_audio_file(
    owner: httpx.AsyncClient, gw: httpx.AsyncClient, sessions: Any
) -> None:
    await install(gw, ASR)
    response = await owner.post(
        f"/api/v1/models/{ASR}/transcriptions", files=_upload(), data={"language": "en"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == mock_transcript("memo.wav", len(AUDIO))
    assert body["modelId"] == ASR and body["language"] == "en"
    model = (await owner.get(f"/api/v1/models/{ASR}")).json()
    assert model["memoryState"] == "READY" and model["activeLeases"] == []

    async with sessions() as db:
        events = (
            await db.scalars(select(AuditEvent).where(AuditEvent.action == "model.transcribe"))
        ).all()
    assert [(e.target_id, e.outcome) for e in events] == [(ASR, "success")]
    assert events[0].metadata_ == {"bytes": len(AUDIO), "language": "en"}


async def test_transcription_errors_reach_the_owner(
    owner: httpx.AsyncClient, gw: httpx.AsyncClient
) -> None:
    missing = await owner.post(f"/api/v1/models/{ASR}/transcriptions", files=_upload())
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "MODEL_NOT_INSTALLED"
    await install(gw)
    chat_model = await owner.post(
        "/api/v1/models/local.general.small/transcriptions", files=_upload()
    )
    assert chat_model.status_code == 422
    assert chat_model.json()["error"]["code"] == "MODEL_CAPABILITY_UNSUPPORTED"
    wrong_type = await owner.post(
        f"/api/v1/models/{ASR}/transcriptions", files=_upload("notes.txt")
    )
    assert wrong_type.status_code == 422
    assert wrong_type.json()["error"]["code"] == "UNSUPPORTED_AUDIO_TYPE"
    empty = await owner.post(f"/api/v1/models/{ASR}/transcriptions", files=_upload(audio=b""))
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "EMPTY_AUDIO"


async def test_transcription_needs_the_owner_and_csrf(
    owner: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    csrf = owner.headers.pop("X-CSRF-Token")
    no_csrf = await owner.post(f"/api/v1/models/{ASR}/transcriptions", files=_upload())
    assert no_csrf.status_code == 403
    owner.headers["X-CSRF-Token"] = csrf
    owner.cookies.clear()
    signed_out = await owner.post(f"/api/v1/models/{ASR}/transcriptions", files=_upload())
    assert signed_out.status_code == 401


async def test_transcription_upload_has_its_own_body_limit(
    owner: httpx.AsyncClient, gw: httpx.AsyncClient
) -> None:
    await install(gw, ASR)
    # Over the global 2 MiB limit, under the 25 MiB upload limit: accepted.
    big = await owner.post(
        f"/api/v1/models/{ASR}/transcriptions", files=_upload(audio=bytes(3 * 1024 * 1024))
    )
    assert big.status_code == 200, big.text[:200]
    huge = await owner.post(
        f"/api/v1/models/{ASR}/transcriptions",
        content=b"x" * (26 * 1024 * 1024),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert huge.status_code == 413 and huge.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
