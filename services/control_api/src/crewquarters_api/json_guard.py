"""Reject JSON request bodies that parse but cannot be processed safely.

Python's JSON parser accepts nesting thousands of levels deep, but redaction, JSON Schema
validation and response serialization recurse once per level, and fail (500) long before
that. PostgreSQL ``jsonb`` and ``text`` cannot store the NUL character (``\\u0000``), so a
payload carrying one fails at insert time (500). :class:`JsonBodyGuard` answers both with
``422 VALIDATION_FAILED`` before the request reaches a route. Both were found by
``tests/fuzz/test_fuzz_control_api_limits.py``.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_JSON_DEPTH = 64
_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def json_problem(value: Any, max_depth: int = MAX_JSON_DEPTH) -> str | None:
    """Why ``value`` (parsed JSON) cannot be accepted, or ``None``. Iterative, so it is
    safe on any depth the parser returns."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, str):
            if "\x00" in node:
                return "JSON strings may not contain the NUL character (\\u0000)."
        elif isinstance(node, dict | list):
            if depth >= max_depth:
                return f"JSON may be nested at most {max_depth} levels deep."
            if isinstance(node, dict):
                for key, item in node.items():
                    if "\x00" in key:
                        return "JSON strings may not contain the NUL character (\\u0000)."
                    stack.append((item, depth + 1))
            else:
                stack.extend((item, depth + 1) for item in node)
    return None


def body_problem(body: bytes, max_depth: int = MAX_JSON_DEPTH) -> str | None:
    """Like :func:`json_problem` for a raw body. A body that is not JSON at all is left to
    the framework, which answers 422 itself."""
    if not body:
        return None
    try:
        value = json.loads(body)
    except RecursionError:
        return f"JSON may be nested at most {max_depth} levels deep."
    except ValueError:
        return None
    return json_problem(value, max_depth)


def _is_json(scope: Scope) -> bool:
    for name, value in scope.get("headers") or []:
        if name == b"content-type":
            media = bytes(value).split(b";", 1)[0].strip().lower()
            return media == b"application/json" or media.endswith(b"+json")
    return True  # FastAPI parses a body without a content type as JSON


class JsonBodyGuard:
    """ASGI middleware: 422 for JSON bodies nested deeper than ``max_depth`` or carrying
    NUL characters. Install it inside the body-size limit, which bounds what it buffers."""

    def __init__(self, app: ASGIApp, max_depth: int = MAX_JSON_DEPTH) -> None:
        self.app = app
        self.max_depth = max_depth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # A NUL in a path parameter or query value reaches a text column lookup (500).
        query = bytes(scope.get("query_string") or b"").lower()
        if "\x00" in str(scope.get("path", "")) or b"%00" in query or b"\x00" in query:
            await _reject(send, "Paths and query strings may not contain the NUL character.")
            return
        if scope.get("method") not in _METHODS or not _is_json(scope):
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the client went away
                await self.app(scope, _replay([], message, receive), send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        problem = body_problem(body, self.max_depth)
        if problem is None:
            await self.app(scope, _replay([body], None, receive), send)
            return
        await _reject(send, problem)


async def _reject(send: Send, problem: str) -> None:
    payload = {
        "error": {
            "code": "VALIDATION_FAILED",
            "message": "The request is invalid.",
            "requestId": None,
            "details": {"errors": [{"path": "/", "message": problem}]},
        }
    }
    await send(
        {
            "type": "http.response.start",
            "status": 422,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": json.dumps(payload).encode()})


def _replay(bodies: list[bytes], last: Message | None, receive: Receive) -> Receive:
    pending: list[Message] = [
        {"type": "http.request", "body": b, "more_body": False} for b in bodies
    ]
    if last is not None:
        pending.append(last)

    async def replayed() -> Message:
        if pending:
            return pending.pop(0)
        return await receive()

    return replayed
