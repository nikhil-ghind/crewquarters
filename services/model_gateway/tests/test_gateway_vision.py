"""Images in chat: only on user messages, only for models whose catalog entry lists vision,
and sent to vLLM as OpenAI ``image_url`` parts."""

from __future__ import annotations

import base64

import httpx
from gateway_helpers import SMALL, VISION, chat_body, install

from crewquarters_gateway.adapters import ChatRequest, LocalAdapter

IMAGE = {"mediaType": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()}


def vision_body(profile: str = VISION, **message: object) -> dict[str, object]:
    user = {"role": "user", "content": "Is anyone there?", "images": [IMAGE], **message}
    return chat_body(profile=profile, messages=[user])


async def test_vision_model_accepts_images(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client, VISION)
    response = await gw_client.post("/internal/v1/llm/chat", json=vision_body())
    assert response.status_code == 200, response.text


async def test_text_model_refuses_images(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client, SMALL)
    response = await gw_client.post("/internal/v1/llm/chat", json=vision_body(SMALL))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_CAPABILITY_UNSUPPORTED"


async def test_images_only_on_user_messages(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client, VISION)
    for message in ({"role": "system"}, {"images": [{"mediaType": "image/gif", "data": "x"}]}):
        response = await gw_client.post("/internal/v1/llm/chat", json=vision_body(**message))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_MESSAGES"


def test_local_adapter_sends_images_as_data_urls() -> None:
    adapter = LocalAdapter("http://model:8000", "served", timeout=5)
    request = ChatRequest(
        messages=[
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Anyone?", "images": [IMAGE]},
        ],
        max_output_tokens=20,
    )
    system, user = adapter._body(request, stream=False)["messages"]
    assert system == {"role": "system", "content": "Be brief."}
    assert user == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Anyone?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IMAGE['data']}"}},
        ],
    }
