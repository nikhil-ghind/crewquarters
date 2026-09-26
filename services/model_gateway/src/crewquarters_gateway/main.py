"""Model gateway service: internal API, model job worker, and leader-elected reaper.

Routes (``/internal/v1``, service credential required; never routed by the proxy):

    GET    /health                     liveness
    GET    /models                     catalog with install/residency/lease state
    GET    /models/{id}
    GET    /models/{id}/events         SSE: a snapshot whenever the model's state changes
    POST   /models/{id}/install        start/resume the pinned download
    POST   /models/{id}/install/cancel {"clear": bool}
    DELETE /models/{id}/files
    POST   /models/{id}/load           manual load (admission control applies)
    POST   /models/{id}/unload         {"force": bool}
    GET    /memory                     reserve / serving limit / reservations / host free
    POST   /leases                     {"modelId", "holderType": "chat", "holderId", "label"}
    DELETE /leases/holders/{type}/{id} release a holder's leases
    POST   /llm/chat                   normalized request; "stream": true -> SSE;
                                       "idempotencyKey" -> replayed for duplicates
    POST   /audio/transcriptions       raw audio body; ?modelId=&filename=&language=
                                       [&holderId=]; whole-file speech-to-text
    POST   /audio/speech               {"modelId", "input", "voice"[, "holderId"]} -> WAV
    WS     /audio/speech/stream        ?modelId=&voice=[&holderId=]; streaming speech:
                                       text in ({"type": "text"|"end"|"cancel"}), PCM out

The audio routes, local chat and chat leases need a second credential besides the service
token: the control API's chat token (owner requests) or the capability broker's voice token
(a phone call's realtime loop; ``holderId`` names the call and its leases).
    POST   /provider-profiles/{id}/test  check a stored OpenAI/Anthropic key
    GET    /metrics                    Prometheus text

Every response carries ``X-Request-Id`` (the caller's, when it sends a sane one); error
envelopes and ChatResponses without a provider request ID use it as ``requestId``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from crewquarters_gateway import catalog, credentials
from crewquarters_gateway.adapters import SpeechRequest, SpeechStream, TranscriptionRequest
from crewquarters_gateway.config import GatewaySettings, get_gateway_settings
from crewquarters_gateway.control import ControlApiClient
from crewquarters_gateway.credentials import CloudProfile, CredentialProvider
from crewquarters_gateway.idempotency import IdempotencyStore, request_key
from crewquarters_gateway.inference import Caller, InferenceService
from crewquarters_gateway.manager import JOB_LOAD, JOB_UNLOAD, ModelManager
from crewquarters_gateway.runtime import DaemonModelRuntime, InProcessModelRuntime, ModelRuntime
from crewquarters_secret_store.db import ProviderProfile
from crewquarters_shared import audit, jobs
from crewquarters_shared.config import Settings, get_settings
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.errors import PlatformError, invalid, not_found
from crewquarters_shared.logs import configure_logging
from crewquarters_shared.metrics import CONTENT_TYPE

log = logging.getLogger("crewquarters.gateway")
PREFIX = "/internal/v1"
LANGUAGE = re.compile(r"[a-z]{2}")
MAX_SPEECH_CHARS = 1000
LEADER_LOCK_KEY = 0x4351474D  # "CQGM"


class GatewayMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "cq_llm_requests_total",
            "LLM requests.",
            ["provider", "outcome"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "cq_llm_request_seconds",
            "LLM request latency.",
            ["provider"],
            buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
            registry=self.registry,
        )
        self.tokens = Counter(
            "cq_llm_tokens_total",
            "Tokens by provider and direction.",
            ["provider", "direction"],
            registry=self.registry,
        )
        self.model_state = Gauge(
            "cq_model_resident", "1 when a model is READY.", ["model"], registry=self.registry
        )
        self.reserved = Gauge(
            "cq_model_reserved_bytes", "Reserved serving memory.", registry=self.registry
        )


class Gateway:
    def __init__(
        self,
        shared: Settings,
        settings: GatewaySettings,
        runtime: ModelRuntime | None = None,
        credentials: CredentialProvider | None = None,
        control: ControlApiClient | None = None,
    ) -> None:
        self.shared = shared
        self.settings = settings
        self.engine = create_engine(shared.database_url, shared.db_pool_size)
        self.sessions = session_factory(self.engine)
        token = shared.internal_service_token.get_secret_value()
        self.runtime = runtime or (
            InProcessModelRuntime()
            if settings.runtime == "inprocess"
            else DaemonModelRuntime(settings.runtime_socket, token)
        )
        self.manager = ModelManager(self.sessions, self.runtime, settings)
        self.control = control or ControlApiClient(settings.control_api_url, token)
        self.credentials = credentials or _credentials_from_settings(self.sessions, settings)
        self.inference = InferenceService(
            self.sessions,
            self.manager,
            self.control,
            self.credentials,
            settings,
            shared.capability_signing_key.get_secret_value(),
        )
        self.idempotency = IdempotencyStore(
            settings.idempotency_ttl_seconds, settings.idempotency_max_entries
        )
        self.metrics = GatewayMetrics()
        self.worker_id = f"gateway:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            JOB_LOAD: self.manager.handle_load,
            JOB_UNLOAD: self.manager.handle_unload,
        }

    async def sync_catalog(self) -> list[str]:
        async with self.sessions() as db, db.begin():
            return await catalog.sync(db, self.settings.catalog_dir)

    # --- background work -----------------------------------------------------------------

    async def run_job_once(self) -> bool:
        async with self.sessions() as db, db.begin():
            job = await jobs.claim(
                db, self.worker_id, self.settings.job_lease_seconds, list(self.handlers)
            )
        if job is None:
            return False

        async def keep_lease() -> None:
            while True:
                await asyncio.sleep(max(1.0, self.settings.job_lease_seconds / 3))
                async with self.sessions() as db, db.begin():
                    await jobs.heartbeat(
                        db, job.id, self.worker_id, self.settings.job_lease_seconds
                    )

        keeper = asyncio.create_task(keep_lease())
        try:
            await self.handlers[job.type](job.payload)
        except Exception as exc:
            log.exception("model job failed", extra={"event": "job.failed", "job_type": job.type})
            async with self.sessions() as db, db.begin():
                await jobs.fail(
                    db, job.id, self.worker_id, {"code": type(exc).__name__}, retryable=True
                )
        else:
            async with self.sessions() as db, db.begin():
                await jobs.complete(db, job.id, self.worker_id)
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
        return True

    async def worker_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.run_job_once()
            except Exception:
                log.exception("gateway worker error")
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=0.25)

    async def leader_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with self.engine.connect() as conn:
                    if not await conn.scalar(
                        text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_LOCK_KEY}
                    ):
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(stop.wait(), timeout=5)
                        continue
                    while not stop.is_set():
                        async with self.sessions() as db, db.begin():
                            await jobs.reap_expired(db)
                        await self.manager.reap(self.control.run_is_active)
                        await self.update_gauges()
                        await conn.execute(text("SELECT 1"))
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(
                                stop.wait(), timeout=self.settings.reaper_interval_seconds
                            )
            except Exception:
                log.exception("gateway leader loop error")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=2)

    async def test_provider_profile(self, profile_id: str) -> dict[str, Any]:
        """Check a stored OpenAI/Anthropic key with one minimal authenticated call and
        record the outcome. Definite rejections (401/403, unreadable key) mark the profile
        ``ERROR``; transient failures (rate limits, provider outages, network) are reported
        as ``ERROR`` but do not demote a profile that was ``CONNECTED``."""
        try:
            pid = uuid.UUID(profile_id)
        except ValueError:
            raise not_found("Provider profile", profile_id) from None
        async with self.sessions() as db:
            row = await db.get(ProviderProfile, pid)
            if row is None or row.provider not in credentials.CLOUD_PROVIDERS:
                raise not_found("Provider profile", profile_id)
            profile, previous = CloudProfile.from_row(row), row.status
        status, detail, definite = "CONNECTED", None, False
        key = await self.credentials.api_key(profile.provider, profile, cached=False)
        if not key:
            status, definite = "ERROR", True
            detail = getattr(self.credentials, "reason", None) or "The stored key cannot be read."
        else:
            adapter = self.inference.adapter_for(
                profile.provider,
                key,
                profile.allowed_models[0] if profile.allowed_models else "",
                self.settings.provider_test_timeout_seconds,
                max_retries=0,
            )
            try:
                await adapter.verify()
            except PlatformError as exc:
                provider_status = exc.details.get("providerStatus")
                status = "ERROR"
                definite = provider_status in (401, 403)
                detail = (
                    "The provider rejected the key."
                    if definite
                    else f"{exc.message} Try again later."
                )
        del key
        if isinstance(self.credentials, credentials.SecretStoreCredentials):
            self.credentials.forget(profile.secret_id)
        checked = datetime.now(UTC)
        # A transient failure does not demote a working profile out of routing.
        transient = status == "ERROR" and not definite and previous == "CONNECTED"
        stored = previous if transient else status
        async with self.sessions() as db, db.begin():
            row = await db.get(ProviderProfile, pid)
            if row is None:
                raise not_found("Provider profile", profile_id)
            row.status = stored
            row.last_checked_at = checked
            audit.record(
                db,
                action=f"connection.{profile.provider}.tested",
                actor_type="service",
                actor_id="model-gateway",
                target_type="provider_profile",
                target_id=pid,
                outcome="success" if status == "CONNECTED" else "failure",
                metadata={"status": status},
            )
        log.info(
            "provider profile tested",
            extra={
                "event": "provider_profile.tested",
                "provider": profile.provider,
                "profile_id": str(pid),
                "status": status,
            },
        )
        return {"status": status, "detail": detail, "checkedAt": checked.isoformat()}

    async def update_gauges(self) -> None:
        models = await self.manager.list_models()
        for model in models:
            self.metrics.model_state.labels(model["id"]).set(
                1 if model["memoryState"] == "READY" else 0
            )
        self.metrics.reserved.set(sum(m["reservedBytes"] for m in models))

    async def close(self) -> None:
        await self.runtime.close()
        await self.control.close()
        await self.engine.dispose()


def create_app(gateway: Gateway | None = None, *, background: bool = True) -> FastAPI:
    gw = gateway or Gateway(get_settings(), get_gateway_settings())
    stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        loaded = await gw.sync_catalog()
        log.info("model catalog synced: %s", ", ".join(loaded))
        tasks = []
        if background:
            tasks = [
                asyncio.create_task(gw.worker_loop(stop)),
                asyncio.create_task(gw.leader_loop(stop)),
            ]
        yield
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await gw.close()

    app = FastAPI(
        title="Crewquarters Model Gateway",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.gateway = gw

    def error_body(request: Request, code: str, message: str, details: Any) -> dict[str, Any]:
        return {
            "error": {
                "code": code,
                "message": message,
                "requestId": _request_id(request),
                "details": details,
            }
        }

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        headers = {"X-Request-Id": _request_id(request)}
        return JSONResponse(
            error_body(request, exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            error_body(request, code, str(exc.detail), {}),
            status_code=exc.status_code,
            headers={"X-Request-Id": _request_id(request)},
        )

    app.add_middleware(RequestIdMiddleware)

    async def service_auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        expected = gw.shared.internal_service_token.get_secret_value()
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode(), expected.encode()):
            raise PlatformError("UNAUTHENTICATED", "Service credential required.", 401)

    async def chat_client_auth(request: Request) -> None:
        """Chat leases and chat inference are reserved for the control API, which holds a
        separate credential; other services (broker, daemon) share only the service token."""
        if _client_kind(request.headers) != "chat":
            raise PlatformError(
                "UNAUTHENTICATED", "Chat requests must come from the control API.", 401
            )

    def _client_kind(headers: Headers) -> str | None:
        """Which second credential the caller holds: "chat" (control API), "voice"
        (capability broker), or None."""
        for kind, header, secret in (
            ("chat", "x-chat-client-token", gw.shared.chat_client_token),
            ("voice", "x-voice-client-token", gw.shared.voice_client_token),
        ):
            sent = headers.get(header, "")
            if sent and hmac.compare_digest(sent.encode(), secret.get_secret_value().encode()):
                return kind
        return None

    async def client_auth(request: Request) -> None:
        kind = _client_kind(request.headers)
        if kind is None:
            raise PlatformError(
                "UNAUTHENTICATED",
                "Audio requests must come from the control API or the capability broker.",
                401,
            )
        request.state.client = kind

    def _audio_caller(request: Request, kind: str, holder_id: str | None) -> Caller:
        if request.state.client == "voice":
            if not holder_id:
                raise invalid("HOLDER_REQUIRED", "Voice requests must name their call (holderId).")
            return gw.inference.voice_caller(holder_id)
        return gw.inference.owner_caller(kind, request.headers.get("x-actor-id"))

    auth = [Depends(service_auth)]
    chat_auth = [Depends(service_auth), Depends(chat_client_auth)]
    audio_auth = [Depends(service_auth), Depends(client_auth)]

    @app.get(f"{PREFIX}/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{PREFIX}/models", dependencies=auth)
    async def list_models() -> list[dict[str, Any]]:
        return await gw.manager.list_models()

    @app.get(f"{PREFIX}/models/{{model_id}}", dependencies=auth)
    async def get_model(model_id: str) -> dict[str, Any]:
        return await gw.manager.get_model(model_id)

    @app.get(f"{PREFIX}/models/{{model_id}}/events", dependencies=auth)
    async def model_events(model_id: str, request: Request) -> StreamingResponse:
        await gw.manager.get_model(model_id)

        async def stream() -> AsyncIterator[str]:
            last = None
            seq = 0
            idle = 0.0
            while not await request.is_disconnected():
                snapshot = await gw.manager.get_model(model_id)
                encoded = json.dumps(snapshot, sort_keys=True)
                if encoded != last:
                    seq += 1
                    last = encoded
                    idle = 0.0
                    yield f"id: {seq}\nevent: model.state\ndata: {encoded}\n\n"
                elif idle >= 15:
                    idle = 0.0
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
                idle += 0.5

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @app.post(f"{PREFIX}/models/{{model_id}}/install", dependencies=auth, status_code=202)
    async def install(model_id: str, request: Request) -> dict[str, Any]:
        return await gw.manager.install(model_id, request.headers.get("x-actor-id"))

    @app.post(f"{PREFIX}/models/{{model_id}}/install/cancel", dependencies=auth)
    async def cancel_install(model_id: str, request: Request) -> dict[str, Any]:
        body = await _json(request)
        return await gw.manager.cancel_install(model_id, bool(body.get("clear", False)))

    @app.delete(f"{PREFIX}/models/{{model_id}}/files", dependencies=auth)
    async def delete_files(model_id: str, request: Request) -> dict[str, Any]:
        return await gw.manager.delete_files(model_id, request.headers.get("x-actor-id"))

    @app.post(f"{PREFIX}/models/{{model_id}}/load", dependencies=auth, status_code=202)
    async def load(model_id: str, request: Request) -> dict[str, Any]:
        return await gw.manager.manual_load(model_id, request.headers.get("x-actor-id") or "owner")

    @app.post(f"{PREFIX}/models/{{model_id}}/unload", dependencies=auth, status_code=202)
    async def unload(model_id: str, request: Request) -> dict[str, Any]:
        body = await _json(request)
        return await gw.manager.unload(
            model_id, bool(body.get("force", False)), request.headers.get("x-actor-id")
        )

    @app.get(f"{PREFIX}/memory", dependencies=auth)
    async def memory() -> dict[str, Any]:
        return await gw.manager.memory()

    @app.post(f"{PREFIX}/leases", dependencies=chat_auth, status_code=201)
    async def create_lease(request: Request) -> dict[str, Any]:
        body = await _json(request)
        if body.get("holderType") != "chat":
            raise invalid(
                "INVALID_HOLDER",
                "Only chat leases are created directly; runs lease through /llm/chat.",
            )
        lease = await gw.manager.acquire(
            str(body["modelId"]),
            "chat",
            str(body["holderId"]),
            str(body.get("label") or "Chat"),
            gw.settings.chat_lease_ttl_seconds,
        )
        return {
            "id": str(lease.id),
            "modelId": lease.model_id,
            "expiresAt": lease.expires_at.isoformat(),
        }

    @app.delete(f"{PREFIX}/leases/holders/{{holder_type}}/{{holder_id}}", dependencies=audio_auth)
    async def release_holder(request: Request, holder_type: str, holder_id: str) -> dict[str, int]:
        # The broker may release only its own voice-call leases.
        if request.state.client == "voice" and not (
            holder_type == "manual" and holder_id.startswith("voice:")
        ):
            raise PlatformError("PERMISSION_DENIED", "Only voice-call leases.", 403)
        return {"released": await gw.manager.release_holder(holder_type, holder_id, "released")}

    @app.post(f"{PREFIX}/llm/chat", dependencies=auth, response_model=None)
    async def llm_chat(request: Request) -> Any:
        body = await _json(request)
        if "x-capability-token" in request.headers:  # present (even empty) => a run caller
            caller = await gw.inference.run_caller(request.headers["x-capability-token"])
        else:
            kind = _client_kind(request.headers)
            holder = body.get("holder") or {}
            if kind == "voice" and holder.get("type") == "voice" and holder.get("id"):
                caller = gw.inference.voice_caller(str(holder["id"]))
            else:
                await chat_client_auth(request)
                if holder.get("type") != "chat" or not holder.get("id"):
                    raise PlatformError(
                        "UNAUTHENTICATED", "Runs must present a capability token.", 401
                    )
                caller = gw.inference.chat_caller(
                    str(holder["id"]), str(holder.get("label") or "Chat")
                )
        rid = _request_id(request)
        key = request_key(caller.holder_type, caller.holder_id, body)
        if not body.get("stream"):

            async def once() -> dict[str, Any]:
                response = await gw.inference.chat(caller, body)
                _observe(gw, response)
                return _with_request_id(response, rid)

            if key is None:
                return await once()
            return await gw.idempotency.run(key, body, once)
        # A keyed stream holds its key until the stream ends (released in events()).
        claim = contextlib.ExitStack()
        if key is not None:
            claim.enter_context(gw.idempotency.claim_stream(key, body))
        try:
            # Prepare (authorize, reserve, load) before the 200 is sent so failures are
            # ordinary HTTP errors rather than a truncated stream.
            prepared = await gw.inference.prepare(caller, body)
        except BaseException:
            claim.close()
            raise

        async def events() -> AsyncIterator[str]:
            try:
                async for event in gw.inference.stream(prepared):
                    if event["type"] == "done":
                        event["response"] = _with_request_id(event["response"], rid)
                        _observe(gw, event["response"])
                    yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
            finally:
                claim.close()

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @app.post(f"{PREFIX}/audio/transcriptions", dependencies=audio_auth)
    async def audio_transcriptions(
        request: Request,
        model_id: str = Query(alias="modelId"),
        filename: str = "audio",
        language: str | None = None,
        holder_id: str | None = Query(None, alias="holderId"),
    ) -> dict[str, Any]:
        audio = await request.body()
        if not audio:
            raise invalid("EMPTY_AUDIO", "The request body must contain the audio file.")
        if len(audio) > gw.settings.max_audio_bytes:
            raise PlatformError(
                "PAYLOAD_TOO_LARGE",
                "The audio file is too large.",
                413,
                {"maxBytes": gw.settings.max_audio_bytes},
            )
        if language is not None and not LANGUAGE.fullmatch(language):
            raise invalid("INVALID_LANGUAGE", "language must be an ISO 639-1 code such as 'en'.")
        transcription = TranscriptionRequest(
            audio=audio,
            filename=filename[:200] or "audio",
            content_type=request.headers.get("content-type") or "application/octet-stream",
            language=language,
        )
        caller = _audio_caller(request, "transcription", holder_id)
        started = asyncio.get_running_loop().time()
        response = await gw.inference.transcribe(model_id, transcription, caller)
        gw.metrics.requests.labels("local", "ok").inc()
        gw.metrics.latency.labels("local").observe(asyncio.get_running_loop().time() - started)
        return _with_request_id(response, _request_id(request))

    @app.post(f"{PREFIX}/audio/speech", dependencies=audio_auth, response_model=None)
    async def audio_speech(request: Request) -> Response:
        body = await _json(request)
        text = str(body.get("input") or "").strip()
        if not text or len(text) > MAX_SPEECH_CHARS:
            raise invalid("INVALID_INPUT", f"input must be 1-{MAX_SPEECH_CHARS} characters.")
        caller = _audio_caller(request, "speech", body.get("holderId"))
        result = await gw.inference.speak(
            str(body.get("modelId") or ""),
            SpeechRequest(text=text, voice=str(body.get("voice") or "female")),
            caller,
        )
        gw.metrics.requests.labels("local", "ok").inc()
        gw.metrics.latency.labels("local").observe(result.latency_ms / 1000)
        return Response(
            result.wav,
            media_type="audio/wav",
            headers={
                "X-Audio-Seconds": str(result.audio_seconds),
                "X-Latency-Ms": str(result.latency_ms),
                "X-Request-Id": _request_id(request),
            },
        )

    @app.websocket(f"{PREFIX}/audio/speech/stream")
    async def audio_speech_stream(
        ws: WebSocket,
        model_id: str = Query(alias="modelId"),
        voice: str = "female",
        holder_id: str | None = Query(None, alias="holderId"),
    ) -> None:
        """Proxy a streaming synthesis between the client and the model container, holding
        the caller's lease for its duration. Errors before audio are sent as
        {"type": "error", "error": {...}} before the socket closes."""
        scheme, _, value = ws.headers.get("authorization", "").partition(" ")
        expected = gw.shared.internal_service_token.get_secret_value()
        kind = _client_kind(ws.headers)
        if (
            scheme.lower() != "bearer"
            or not hmac.compare_digest(value.encode(), expected.encode())
            or kind is None
        ):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            if kind == "voice":
                if not holder_id:
                    raise invalid("HOLDER_REQUIRED", "Voice requests must name their call.")
                caller = gw.inference.voice_caller(holder_id)
            else:
                caller = gw.inference.owner_caller("speech", ws.headers.get("x-actor-id"))
            async with gw.inference.speech_stream(model_id, voice, caller) as stream:
                await _pump_speech(ws, stream)
        except PlatformError as exc:
            with contextlib.suppress(Exception):
                await ws.send_json(
                    {
                        "type": "error",
                        "error": {"code": exc.code, "message": exc.message, "details": exc.details},
                    }
                )
        except WebSocketDisconnect:
            return
        with contextlib.suppress(Exception):
            await ws.close()

    @app.post(f"{PREFIX}/provider-profiles/{{profile_id}}/test", dependencies=auth)
    async def test_provider_profile(profile_id: str) -> dict[str, Any]:
        return await gw.test_provider_profile(profile_id)

    @app.get(f"{PREFIX}/metrics", dependencies=auth)
    async def metrics() -> PlainTextResponse:
        await gw.update_gauges()
        return PlainTextResponse(generate_latest(gw.metrics.registry), media_type=CONTENT_TYPE)

    return app


def _credentials_from_settings(sessions: Any, settings: GatewaySettings) -> CredentialProvider:
    return credentials.from_settings(
        sessions, settings.master_key_file, settings.credential_cache_seconds
    )


def _sane_request_id(incoming: str) -> str:
    """The caller's X-Request-Id when it is sane, else a new one."""
    if 8 <= len(incoming) <= 128 and incoming.isascii() and incoming.isprintable():
        return incoming
    return uuid.uuid4().hex


class RequestIdMiddleware:
    """Pure ASGI (streams are passed through untouched): assigns the request ID and
    echoes it in ``X-Request-Id``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _sane_request_id(Headers(scope=scope).get("x-request-id", ""))
        scope.setdefault("state", {})["request_id"] = rid

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-Id"] = rid
            await send(message)

        await self.app(scope, receive, send_with_id)


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if existing:
        return str(existing)
    rid = _sane_request_id(request.headers.get("x-request-id", ""))
    request.state.request_id = rid
    return rid


def _with_request_id(response: dict[str, Any], rid: str) -> dict[str, Any]:
    if not response.get("requestId"):
        response = {**response, "requestId": rid}
    return response


def _observe(gw: Gateway, response: dict[str, Any]) -> None:
    provider = response.get("provider", "local")
    gw.metrics.requests.labels(provider, "ok").inc()
    gw.metrics.latency.labels(provider).observe((response.get("latencyMs") or 0) / 1000)
    usage = response.get("usage") or {}
    gw.metrics.tokens.labels(provider, "input").inc(usage.get("inputTokens", 0))
    gw.metrics.tokens.labels(provider, "output").inc(usage.get("outputTokens", 0))


async def _pump_speech(ws: WebSocket, stream: SpeechStream) -> None:
    """Client text -> model; model audio and events -> client, until the model is done."""

    async def upstream() -> None:
        try:
            while True:
                message = await ws.receive_json()
                kind = message.get("type") if isinstance(message, dict) else None
                if kind == "text":
                    await stream.send_text(str(message.get("text") or ""))
                elif kind == "end":
                    await stream.end()
                elif kind == "cancel":
                    await stream.cancel()
                    return
        except (WebSocketDisconnect, RuntimeError, ValueError):
            await stream.cancel()

    reader = asyncio.create_task(upstream())
    try:
        async for event in stream.events():
            if isinstance(event, bytes):
                await ws.send_bytes(event)
            else:
                await ws.send_json(event)
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader


async def _json(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 2 * 1024 * 1024:
        raise PlatformError("PAYLOAD_TOO_LARGE", "Request body too large.", 413)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise invalid("INVALID_JSON", "The request body is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise invalid("INVALID_JSON", "The request body must be a JSON object.")
    return data


def run() -> None:
    import uvicorn

    configure_logging("model-gateway")
    uvicorn.run(
        "crewquarters_gateway.main:create_app",
        factory=True,
        host=os.environ.get("CQ_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.environ.get("CQ_GATEWAY_PORT", "8090")),
        log_config=None,
        access_log=False,
    )
