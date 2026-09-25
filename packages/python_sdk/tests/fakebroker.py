"""A scripted in-memory broker for SDK unit tests (served through httpx.MockTransport).

Its semantics mirror the control plane's /internal/v1 run API, which the capability broker passes
through: results are ``succeeded`` or ``failed``; the first claim of an action key is ``claimed``
and any later claim of an uncompleted key is ``in_doubt``; input requests are polled by id.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

import httpx

PREFIX = "/internal/v1/sdk"
STATE_FOR_STATUS = {"succeeded": "SUCCEEDED", "failed": "FAILED"}


def error(status: int, code: str, message: str = "") -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": message or code.lower()}}
    )


class FakeBroker:
    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        capabilities: tuple[str, ...] = ("user_input", "events.write", "idempotency"),
        llm_profiles: tuple[str, ...] = ("local.general.small",),
        heartbeat_interval: float = 0.01,
        input_wait_remaining: float = 3600,
        trigger: str = "manual",
        scheduled_for: str | None = None,
    ) -> None:
        self.config = config if config is not None else {}
        self.capabilities = list(capabilities)
        self.llm_profiles = list(llm_profiles)
        self.heartbeat_interval = heartbeat_interval
        self.input_wait_remaining = input_wait_remaining
        self.trigger = trigger
        self.scheduled_for = scheduled_for
        self.attempt = 1
        self.events: list[dict[str, Any]] = []
        self.event_batches: list[int] = []
        self.results: list[dict[str, Any]] = []
        self.heartbeats = 0
        self.cancel_on_heartbeat: int | None = None
        self.input_creates: list[dict[str, Any]] = []
        self.input_script: dict[str, list[dict[str, Any]]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.overrides: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def handshake_body(self) -> dict[str, Any]:
        return {
            "run": {
                "id": "run-1",
                "attempt": self.attempt,
                "trigger": self.trigger,
                "scheduledFor": self.scheduled_for,
                "installationId": "inst-1",
                "agentId": "test-agent",
                "agentVersion": "0.1.0",
                "createdAt": "2026-09-24T10:00:00+00:00",
            },
            "config": self.config,
            "capabilities": self.capabilities,
            "grants": {
                "llmProfiles": self.llm_profiles,
                "modelBindings": {"local.general": "local.general.small"},
                "knowledgeBaseIds": ["kb-1"],
                "google": [],
                "twilio": [],
                "cloudProviders": [],
            },
            "limits": {
                "activeTimeoutSeconds": 600,
                "inputWaitRemainingSeconds": self.input_wait_remaining,
            },
            "heartbeatIntervalSeconds": self.heartbeat_interval,
            "serverTime": "2026-09-24T10:00:00+00:00",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix(PREFIX)
        body: Any = json.loads(request.content) if request.content else None
        override = self.overrides.get((request.method, path))
        if override is not None:
            return override(request)
        if request.method == "POST" and path == "/handshake":
            return httpx.Response(200, json=self.handshake_body())
        if request.method == "POST" and path == "/heartbeat":
            self.heartbeats += 1
            cancel = (
                self.cancel_on_heartbeat is not None and self.heartbeats >= self.cancel_on_heartbeat
            )
            return httpx.Response(200, json={"state": "RUNNING", "cancelRequested": cancel})
        if request.method == "POST" and path == "/events":
            self.event_batches.append(len(body["events"]))
            self.events.extend(body["events"])
            return httpx.Response(
                200, json={"accepted": len(body["events"]), "lastSequence": len(self.events)}
            )
        if request.method == "POST" and path == "/result":
            self.results.append(body)
            return httpx.Response(
                200, json={"state": STATE_FOR_STATUS[body["status"]], "cancelRequested": False}
            )
        if request.method == "POST" and path == "/input-requests":
            self.input_creates.append(body)
            return httpx.Response(200, json=self._input_state(body["key"]))
        if request.method == "GET" and path.startswith("/input-requests/"):
            request_id = unquote(path.removeprefix("/input-requests/"))
            return httpx.Response(200, json=self._input_state(request_id.removeprefix("in-")))
        if request.method == "POST" and path.startswith("/actions/") and path.endswith("/claim"):
            return self._claim(unquote(path.removeprefix("/actions/").removesuffix("/claim")))
        if request.method == "POST" and path.startswith("/actions/") and path.endswith("/complete"):
            key = unquote(path.removeprefix("/actions/").removesuffix("/complete"))
            if key not in self.actions:
                return error(409, "ACTION_NOT_CLAIMED")
            self.actions[key].update(status="completed", result=body.get("result"))
            return httpx.Response(200, json=self.actions[key])
        return error(404, "NOT_FOUND", f"{request.method} {path}")

    def _input_state(self, key: str) -> dict[str, Any]:
        script = self.input_script.setdefault(key, [{"state": "pending"}])
        step = script.pop(0) if len(script) > 1 else script[0]
        return {
            "id": f"in-{key}",
            "key": key,
            "version": 1,
            "createdAt": "2026-09-24T10:00:00Z",
            "deadline": "2026-09-25T10:00:00Z",
            "answer": None,
            "answeredAt": None,
            **step,
        }

    def _claim(self, key: str) -> httpx.Response:
        action = self.actions.get(key)
        if action is None:
            self.actions[key] = {"key": key, "status": "claimed", "result": None}
            return httpx.Response(200, json=self.actions[key])
        if action["status"] == "completed":
            return httpx.Response(200, json=action)
        action["status"] = "in_doubt"
        return httpx.Response(200, json=action)


def answered(value: Any) -> dict[str, Any]:
    return {"state": "answered", "answer": value, "answeredAt": "2026-09-24T10:05:00Z"}
