"""Deterministic OpenAI-compatible mock model server for laptops and CI.

Serves ``GET /v1/models``, ``GET /health`` and ``POST /v1/chat/completions``
(streaming and non-streaming, with ``response_format`` JSON-schema support) so the
model gateway's lease/load/route/unload path runs end to end without a GPU.
Standard library only.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SERVED = "mock"
STARTUP_DELAY = 0.0


def _value_for(schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = schema.get("required", list(props))
        return {k: _value_for(props.get(k, {})) for k in required}
    if kind == "array":
        return []
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return "mock"


# Knowledge-grounded messages: a preamble, then <evidence><passage id=...>text</passage>
# ...</evidence>. Kept identical to crewquarters_gateway.adapters.mock_reply (a test checks).
_EVIDENCE_MARKERS = ("<evidence>", "UNTRUSTED EVIDENCE")
_FIRST_PASSAGE = re.compile(
    r"<passage\s+id=(?:\"([^\"]*)\"|'([^']*)')[^>]*>(.*?)</passage>", re.DOTALL
)
MOCK_SNIPPET_CHARS = 120


def mock_reply(content: str) -> str:
    """Echo ordinary messages; answer evidence messages with a short cited snippet and
    never repeat the preamble, the delimiters or the whole message."""
    if not any(marker in content for marker in _EVIDENCE_MARKERS):
        return f"Mock reply to: {content[:200]}"
    match = _FIRST_PASSAGE.search(content)
    if match is None:
        return "Mock answer: the evidence did not contain a passage to quote."
    citation = html.unescape(match.group(1) or match.group(2) or "")
    text = " ".join(html.unescape(match.group(3)).split())
    text = text.replace("<", "").replace(">", "")
    if len(text) > MOCK_SNIPPET_CHARS:
        text = text[:MOCK_SNIPPET_CHARS].rsplit(" ", 1)[0] + "..."
    return f'Mock answer from the knowledge base: "{text}" [{citation}]'


def _answer(body: dict[str, Any]) -> str:
    fmt = body.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        return json.dumps(_value_for(fmt.get("json_schema", {}).get("schema", {})))
    if fmt.get("type") == "json_object":
        return "{}"
    last = next(
        (m for m in reversed(body.get("messages", [])) if m.get("role") == "user"), {"content": ""}
    )
    content = last.get("content")
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return mock_reply(str(content))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": SERVED, "object": "model"}]})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if body.get("model") != SERVED:
            self._json(404, {"error": {"message": f"model {body.get('model')} not served"}})
            return
        text = _answer(body)
        prompt_tokens = sum(
            len(str(m.get("content", "")).split()) for m in body.get("messages", [])
        )
        completion_tokens = len(text.split())
        created = int(time.time())
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            words = text.split(" ")
            for i, word in enumerate(words):
                delta = word if i == 0 else " " + word
                chunk = {
                    "id": "mock-1",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": SERVED,
                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            final = {
                "id": "mock-1",
                "object": "chat.completion.chunk",
                "created": created,
                "model": SERVED,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
            self.wfile.write(f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
            self.close_connection = True
            return
        self._json(
            200,
            {
                "id": "mock-1",
                "object": "chat.completion",
                "created": created,
                "model": SERVED,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            },
        )


def main() -> None:
    global SERVED
    parser = argparse.ArgumentParser()
    parser.add_argument("--served-model-name", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--startup-delay", type=float, default=0.0)
    args = parser.parse_args()
    SERVED = args.served_model_name
    time.sleep(args.startup_delay)  # simulates weight loading
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
