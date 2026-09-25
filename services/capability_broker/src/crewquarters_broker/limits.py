"""Request body size limit (PLAN.md section 16.1, resource exhaustion).

Agents are untrusted, so bodies are refused with 413 before they are buffered: by
Content-Length when declared, and by counting chunks when not.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _TooLarge(Exception):
    pass


class BodySizeLimit:
    def __init__(self, app: ASGIApp, limit_for: Callable[[Scope], int]) -> None:
        self.app = app
        self.limit_for = limit_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(send, limit)
            return
        seen = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal seen, too_large
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    too_large = True
                    raise _TooLarge
            return message

        async def guarded_send(message: Message) -> None:
            if not too_large:  # FastAPI turns the read error into a 400; send our 413
                await send(message)

        with contextlib.suppress(_TooLarge):
            await self.app(scope, limited_receive, guarded_send)
        if too_large:
            await self._reject(send, limit)

    @staticmethod
    async def _reject(send: Send, limit: int) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"Request bodies are limited to {limit} bytes.",
                    "requestId": None,
                    "details": {},
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
