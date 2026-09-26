"""Camera snapshots: one still image from the HTTP(S) snapshot URL in the installation config.

The URL comes only from the owner-approved config (``cameraUrl``), never from the agent.
It may carry credentials (``http://user:pass@camera/snapshot.jpg``), so it is never logged
or echoed in errors. Only JPEG and PNG bodies up to :data:`MAX_BYTES` are returned.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlsplit

import httpx

from crewquarters_shared.errors import PlatformError
from crewquarters_shared.timeutil import utcnow

MAX_BYTES = 3 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
_MAGIC = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}


def _config_error(message: str) -> PlatformError:
    return PlatformError("NEEDS_CONFIGURATION", message, 409, {"key": "cameraUrl"})


def _unavailable(message: str) -> PlatformError:
    return PlatformError("PROVIDER_UNAVAILABLE", message, 503, {"provider": "camera"})


def media_type(body: bytes) -> str | None:
    return next((kind for magic, kind in _MAGIC.items() if body.startswith(magic)), None)


async def snapshot(http: httpx.AsyncClient, url: str) -> dict[str, Any]:
    if urlsplit(url).scheme not in ("http", "https"):
        raise _config_error("The camera URL must start with http:// or https://.")
    try:
        async with http.stream("GET", url, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status_code != 200:
                raise _unavailable(f"The camera answered HTTP {resp.status_code}.")
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise _unavailable("The camera image is larger than 3 MB.")
    except httpx.HTTPError:
        raise _unavailable("The camera is unreachable.") from None
    kind = media_type(body)
    if kind is None:
        raise _unavailable("The camera did not return a JPEG or PNG image.")
    return {
        "mediaType": kind,
        "data": base64.b64encode(body).decode(),
        "bytes": len(body),
        "capturedAt": utcnow(),
    }
