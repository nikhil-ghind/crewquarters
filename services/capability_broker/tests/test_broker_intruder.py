"""Camera snapshots, owner-only Gmail alerts, and images in chat (the Intruder Watch agent)."""

from __future__ import annotations

import base64
import email
import email.policy
import json
import uuid
from typing import Any

from crewquarters_broker import fakes

SDK = "/internal/v1/sdk"
CAMERA = "camera.snapshot:config"
SEND = "google.gmail.send"
PNG = fakes.png(4, 4, lambda x, y: 0)
IMAGE = {"mediaType": "image/png", "data": base64.b64encode(PNG).decode()}


async def test_camera_frame_comes_from_the_configured_url(harness: Any) -> None:
    headers = harness.agent([CAMERA], config={"cameraUrl": "http://camera.example.com/snap"})
    resp = await harness.client.get(f"{SDK}/camera/frame", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = base64.b64decode(body["data"])
    assert body["mediaType"] == "image/png" and body["bytes"] == len(data)
    assert data.startswith(b"\x89PNG") and body["capturedAt"]


async def test_camera_frame_needs_the_capability_and_a_url(harness: Any) -> None:
    headers = harness.agent(["events.write"], config={"cameraUrl": "http://camera.example.com/"})
    assert (await harness.client.get(f"{SDK}/camera/frame", headers=headers)).status_code == 403
    headers = harness.agent([CAMERA], config={})
    resp = await harness.client.get(f"{SDK}/camera/frame", headers=headers)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"
    headers = harness.agent([CAMERA], config={"cameraUrl": "file:///etc/passwd"})
    resp = await harness.client.get(f"{SDK}/camera/frame", headers=headers)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"


async def test_camera_that_is_not_an_image_is_refused_without_echoing_the_url(
    harness: Any,
) -> None:
    # Any other host in fake mode is the fake Google, which answers 404.
    url = "http://admin:secret-pass@cam.invalid/snap"
    headers = harness.agent([CAMERA], config={"cameraUrl": url})
    resp = await harness.client.get(f"{SDK}/camera/frame", headers=headers)
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert "secret-pass" not in resp.text


async def test_notify_owner_emails_only_the_connected_address(
    harness: Any, user_id: uuid.UUID
) -> None:
    await harness.connect_google(
        user_id,
        code="fake-code:gmail.readonly,gmail.send",
        capabilities=("gmail.readonly", "gmail.send"),
    )
    headers = harness.agent([SEND])
    resp = await harness.client.post(
        f"{SDK}/google/gmail/notify-owner",
        headers=headers,
        json={"subject": "Intruder alert", "text": "Someone at the door.", "image": IMAGE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "sent-1"
    [sent] = harness.google.sent
    message = email.message_from_bytes(
        base64.urlsafe_b64decode(sent["raw"]), policy=email.policy.default
    )
    assert message["To"] == message["From"] == "owner@example.com"
    assert message["Subject"] == "[Crewquarters] Intruder alert"
    [attachment] = list(message.iter_attachments())
    assert attachment.get_content_type() == "image/png" and attachment.get_content() == PNG


async def test_notify_owner_rules(harness: Any, user_id: uuid.UUID) -> None:
    headers = harness.agent(["google.gmail.readonly"])
    body: dict[str, Any] = {"subject": "Alert", "text": "x"}
    resp = await harness.client.post(f"{SDK}/google/gmail/notify-owner", headers=headers, json=body)
    assert resp.status_code == 403  # no google.gmail.send
    headers = harness.agent([SEND])
    resp = await harness.client.post(
        f"{SDK}/google/gmail/notify-owner", headers=headers, json={**body, "to": "x@example.com"}
    )
    assert resp.status_code == 422  # the agent cannot name a recipient
    resp = await harness.client.post(
        f"{SDK}/google/gmail/notify-owner",
        headers=headers,
        json={**body, "subject": "Alert\r\nBcc: x@example.com"},
    )
    assert resp.status_code == 422  # no header injection
    # Send granted without Gmail read: the broker does not know the owner's address.
    await harness.connect_google(user_id, code="fake-code:gmail.send", capabilities=("gmail.send",))
    resp = await harness.client.post(f"{SDK}/google/gmail/notify-owner", headers=headers, json=body)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONNECTION"
    assert harness.google.sent == []


async def test_chat_forwards_images_on_user_messages(harness: Any) -> None:
    headers = harness.agent(["llm.profile:local.vision.small"])
    chat = {
        "profile": "local.vision",
        "messages": [{"role": "user", "content": "Anyone there?", "images": [IMAGE]}],
    }
    resp = await harness.client.post(f"{SDK}/llm/chat", headers=headers, json=chat)
    assert resp.status_code == 200, resp.text
    [sent] = harness.gateway_requests
    assert json.loads(sent.content)["messages"][0]["images"] == [IMAGE]


async def test_chat_image_rules(harness: Any) -> None:
    headers = harness.agent(["llm.profile:local.vision.small"])

    async def chat(message: dict[str, Any]) -> int:
        body = {"profile": "local.vision", "messages": [message]}
        return (
            await harness.client.post(f"{SDK}/llm/chat", headers=headers, json=body)
        ).status_code

    assert await chat({"role": "system", "content": "x", "images": [IMAGE]}) == 422
    assert await chat({"role": "user", "content": "x", "images": [{**IMAGE, "data": "%%%"}]}) == 422
    wrong = {"mediaType": "image/jpeg", "data": IMAGE["data"]}  # PNG bytes labelled JPEG
    assert await chat({"role": "user", "content": "x", "images": [wrong]}) == 422
    assert await chat({"role": "user", "content": "x", "images": [IMAGE] * 5}) == 422
    assert harness.gateway_requests == []
