"""Camera snapshots from the camera the owner configured (installation config ``cameraUrl``)."""

from __future__ import annotations

from typing import Any

from crewquarters._transport import BrokerClient


class CameraClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def frame(self) -> dict[str, Any]:
        """``{mediaType, data (base64), bytes, capturedAt}``; pass ``{mediaType, data}`` as a
        chat message image or an owner notification image."""
        data: dict[str, Any] = await self._transport.request(
            "GET", "/camera/frame", operation="camera.frame", idempotent=True
        )
        return data
