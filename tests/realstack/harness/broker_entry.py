"""Capability broker entry point for the real-stack E2E suite (TEST ONLY).

It runs the unmodified broker application (``crewquarters_broker.main.create_app``) with
``CQ_PROVIDER_MODE=fake`` and the broker's own fake Google and Twilio
(``crewquarters_broker.fakes``), injected through ``create_app(provider_transport=...)``,
which is the broker's documented seam for tests. On top of those fakes it adds:

* **Signed Twilio callbacks over HTTP.** Instead of calling the telephony service in-process
  (``fakes.call_simulator``), fake Twilio POSTs each callback to the URL the broker gave it
  (``Url``/``StatusCallback``, built from ``CQ_PUBLIC_BASE_URL``), delivered through the
  proxy (``REALSTACK_CALLBACK_BASE``) and signed with the saved auth token, as Twilio does.
  Every status callback is delivered twice, like Twilio retrying a slow webhook.
* **Fault knobs and fixtures** on ``/__realstack/*`` (``X-Realstack-Admin`` header): seed a
  spreadsheet, read sheets and placed calls, make Google time out or fail, add latency,
  expire the Google grant, and make the next Twilio create time out.
* **State that survives a container restart** (``docker compose stop``/``start``): refresh
  grants, sheets and calls are kept in ``/tmp``, so broker-outage tests keep the Google
  connection.

The proxy never forwards ``/__realstack``; the port is published on 127.0.0.1 by
``infra/compose/compose.realstack.yaml`` only.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request

from crewquarters_broker import fakes
from crewquarters_broker.main import create_app
from crewquarters_broker.twilio import signature
from crewquarters_shared.logs import configure_logging

log = logging.getLogger("crewquarters.broker.realstack")
STATE_FILE = Path(os.environ.get("REALSTACK_STATE_FILE", "/tmp/realstack-fakes.json"))  # noqa: S108
ADMIN_TOKEN = os.environ.get("REALSTACK_ADMIN_TOKEN", "")
CALLBACK_BASE = os.environ.get("REALSTACK_CALLBACK_BASE", "http://proxy:8080").rstrip("/")
GATHER_ACTION = re.compile(r'<Gather[^>]*action="([^"]+)"')


class RealstackGoogle(fakes.FakeGoogle):
    """Fake Google with revocable access tokens (the grant-expiry fault)."""

    def __init__(self) -> None:
        super().__init__()
        self.revoked_access: set[str] = set()
        self.issued_access: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if bearer in self.revoked_access:
            return httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        response = super().handle(request)
        if request.url.path == "/token" and response.status_code == 200:
            self.issued_access.add(json.loads(response.content)["access_token"])
        return response

    def expire(self) -> None:
        """What Google does when a testing-mode grant hits its seven-day limit."""
        self.revoked.update(self.refresh_grants)
        self.revoked_access.update(self.issued_access)


class RealstackTwilio(fakes.FakeTwilio):
    """Fake Twilio that remembers what each created call needs for its callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: dict[str, dict[str, str]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        response = super().handle(request)
        if request.method == "POST" and response.status_code == 201:
            auth = request.headers.get("authorization", "").removeprefix("Basic ")
            _, _, token = base64.b64decode(auth).decode().partition(":")
            sid = json.loads(response.content)["sid"]
            form = dict(parse_qsl(request.content.decode()))
            self.pending[sid] = {"token": token, **form}
        return response


class Harness:
    def __init__(self) -> None:
        self.google = RealstackGoogle()
        self.twilio = RealstackTwilio()
        self.faults: dict[str, Any] = {}
        self.callbacks: list[dict[str, Any]] = []
        self.tasks: set[asyncio.Task[None]] = set()
        self.http = httpx.AsyncClient(timeout=30)
        self._load()

    # --- persistence ----------------------------------------------------------------

    def _load(self) -> None:
        if not STATE_FILE.is_file():
            return
        data = json.loads(STATE_FILE.read_text())
        self.google.refresh_grants = data.get("refreshGrants", {})
        self.google.revoked = set(data.get("revoked", []))
        self.google.consented = set(data.get("consented", []))
        self.google.sheets = data.get("sheets", {})
        self.twilio.calls = data.get("calls", [])
        self.callbacks = data.get("callbacks", [])
        log.info("realstack fake state restored from %s", STATE_FILE)

    def save(self) -> None:
        data = {
            "refreshGrants": self.google.refresh_grants,
            "revoked": sorted(self.google.revoked),
            "consented": sorted(self.google.consented),
            "sheets": self.google.sheets,
            "calls": self.twilio.calls,
            "callbacks": self.callbacks,
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(STATE_FILE)

    # --- provider transport with faults ---------------------------------------------

    async def route(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        is_google_api = host in ("gmail.googleapis.com", "sheets.googleapis.com")
        if is_google_api and (latency := float(self.faults.get("googleLatencySeconds") or 0)):
            await asyncio.sleep(latency)
        if is_google_api and (mode := self.faults.get("googleApi")):
            if mode == "timeout":
                raise httpx.ReadTimeout("realstack fault: Google timed out", request=request)
            if mode == "error":
                return httpx.Response(503, json={"error": {"code": 503}})
        if host == "api.twilio.com" and request.method == "POST":
            self.twilio.fail_next = self.faults.pop("twilioCreate", None)
        if host == "api.twilio.com":
            response = self.twilio.handle(request)
        else:
            response = self.google.handle(request)
        if request.method != "GET" or request.url.path == "/token":
            self.save()
        return response

    # --- signed callbacks through the proxy --------------------------------------------

    async def on_created(self, call_id: Any, sid: str, to: str) -> None:
        task = asyncio.create_task(self._play(sid, to))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _post(self, public_url: str, token: str, params: dict[str, str]) -> httpx.Response:
        parts = urlsplit(public_url)
        target = CALLBACK_BASE + parts.path + (f"?{parts.query}" if parts.query else "")
        response = await self.http.post(
            target,
            data=params,
            headers={"X-Twilio-Signature": signature(token, public_url, params)},
        )
        self.callbacks.append(
            {
                "path": parts.path,
                "params": params,
                "status": response.status_code,
                "twiml": response.text
                if "xml" in response.headers.get("content-type", "")
                else None,
            }
        )
        self.save()
        return response

    async def _status(self, call: dict[str, str], sid: str, status: str) -> None:
        params = {"CallSid": sid, "CallStatus": status, "To": call["To"]}
        for _ in range(2):  # Twilio retries webhooks; the broker must treat it as one event
            await self._post(call["StatusCallback"], call["token"], params)

    async def _play(self, sid: str, to: str) -> None:
        call = self.twilio.pending.pop(sid, None)
        if call is None:
            return
        try:
            await asyncio.sleep(0.2)
            digit = to[-1]
            await self._status(call, sid, "ringing")
            if digit in "234":
                final = {"2": "busy", "3": "no-answer", "4": "failed"}[digit]
                await self._status(call, sid, final)
                return
            await self._status(call, sid, "in-progress")
            voice = await self._post(call["Url"], call["token"], {"CallSid": sid, "To": to})
            action = GATHER_ACTION.search(voice.text)
            if digit != "5" and action is not None:
                gather_url = action.group(1).replace("&amp;", "&")
                speech = {"CallSid": sid, "SpeechResult": "Yes, I can attend."}
                await self._post(gather_url, call["token"], speech)
            await self._status(call, sid, "completed")
        except Exception:
            log.exception("realstack fake Twilio callback delivery failed")


def build() -> FastAPI:
    harness = Harness()
    app = create_app(provider_transport=httpx.MockTransport(harness.route))
    app.state.broker.telephony.on_created = harness.on_created

    def admin(token: str) -> None:
        if not ADMIN_TOKEN or not hmac.compare_digest(token, ADMIN_TOKEN):
            raise HTTPException(status_code=404)

    @app.get("/__realstack/state", include_in_schema=False)
    async def state(x_realstack_admin: str = Header("")) -> dict[str, Any]:
        admin(x_realstack_admin)
        return {
            "sheets": harness.google.sheets,
            "calls": harness.twilio.calls,
            "callbacks": harness.callbacks,
            "faults": harness.faults,
            "refreshGrants": len(harness.google.refresh_grants),
        }

    @app.put("/__realstack/sheets/{spreadsheet_id}", include_in_schema=False)
    async def seed_sheet(
        spreadsheet_id: str, request: Request, x_realstack_admin: str = Header("")
    ) -> dict[str, Any]:
        admin(x_realstack_admin)
        harness.google.sheets[spreadsheet_id] = await request.json()
        harness.save()
        return {"spreadsheetId": spreadsheet_id}

    @app.put("/__realstack/faults", include_in_schema=False)
    async def set_faults(request: Request, x_realstack_admin: str = Header("")) -> dict[str, Any]:
        admin(x_realstack_admin)
        harness.faults = {k: v for k, v in (await request.json()).items() if v is not None}
        return harness.faults

    @app.post("/__realstack/google/expire", include_in_schema=False)
    async def expire(x_realstack_admin: str = Header("")) -> dict[str, Any]:
        admin(x_realstack_admin)
        harness.google.expire()
        harness.save()
        return {"revoked": len(harness.google.revoked)}

    @app.post("/__realstack/reset", include_in_schema=False)
    async def reset(x_realstack_admin: str = Header("")) -> dict[str, Any]:
        """Clears sheets, calls, callbacks and faults; keeps Google grants."""
        admin(x_realstack_admin)
        await asyncio.gather(*harness.tasks, return_exceptions=True)
        harness.google.sheets.clear()
        harness.twilio.calls.clear()
        harness.callbacks.clear()
        harness.faults.clear()
        harness.save()
        return {"reset": True, "nonce": secrets.token_hex(4)}

    return app


def main() -> None:
    configure_logging("capability-broker")
    uvicorn.run(
        build(),
        host=os.environ.get("CQ_BROKER_HOST", "0.0.0.0"),  # noqa: S104 - container network
        port=int(os.environ.get("CQ_BROKER_PORT", "8000")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
