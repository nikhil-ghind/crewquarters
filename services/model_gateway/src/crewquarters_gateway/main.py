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
    POST   /llm/chat                   normalized request; "stream": true -> SSE
    GET    /metrics                    Prometheus text
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text

from crewquarters_gateway import catalog
from crewquarters_gateway.config import GatewaySettings, get_gateway_settings
from crewquarters_gateway.control import ControlApiClient
from crewquarters_gateway.credentials import CredentialProvider, NoCredentials
from crewquarters_gateway.inference import InferenceService
from crewquarters_gateway.manager import JOB_LOAD, JOB_UNLOAD, ModelManager
from crewquarters_gateway.runtime import DaemonModelRuntime, InProcessModelRuntime, ModelRuntime
from crewquarters_shared import jobs
from crewquarters_shared.config import Settings, get_settings
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.errors import PlatformError, invalid
from crewquarters_shared.logs import configure_logging
from crewquarters_shared.metrics import CONTENT_TYPE

log = logging.getLogger("crewquarters.gateway")
PREFIX = "/internal/v1"
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
        self.inference = InferenceService(
            self.sessions,
            self.manager,
            self.control,
            credentials or NoCredentials(),
            settings,
            shared.capability_signing_key.get_secret_value(),
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

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "requestId": None,
                    "details": exc.details,
                }
            },
            status_code=exc.status_code,
        )

    async def service_auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        expected = gw.shared.internal_service_token.get_secret_value()
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode(), expected.encode()):
            raise PlatformError("UNAUTHENTICATED", "Service credential required.", 401)

    async def chat_client_auth(request: Request) -> None:
        """Chat leases and chat inference are reserved for the control API, which holds a
        separate credential; other services (broker, daemon) share only the service token."""
        sent = request.headers.get("x-chat-client-token", "")
        expected = gw.shared.chat_client_token.get_secret_value()
        if not sent or not hmac.compare_digest(sent.encode(), expected.encode()):
            raise PlatformError(
                "UNAUTHENTICATED", "Chat requests must come from the control API.", 401
            )

    auth = [Depends(service_auth)]
    chat_auth = [Depends(service_auth), Depends(chat_client_auth)]

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

    @app.delete(f"{PREFIX}/leases/holders/{{holder_type}}/{{holder_id}}", dependencies=chat_auth)
    async def release_holder(holder_type: str, holder_id: str) -> dict[str, int]:
        return {"released": await gw.manager.release_holder(holder_type, holder_id, "released")}

    @app.post(f"{PREFIX}/llm/chat", dependencies=auth, response_model=None)
    async def llm_chat(request: Request) -> Any:
        body = await _json(request)
        if "x-capability-token" in request.headers:  # present (even empty) => a run caller
            caller = await gw.inference.run_caller(request.headers["x-capability-token"])
        else:
            await chat_client_auth(request)
            holder = body.get("holder") or {}
            if holder.get("type") != "chat" or not holder.get("id"):
                raise PlatformError("UNAUTHENTICATED", "Runs must present a capability token.", 401)
            caller = gw.inference.chat_caller(str(holder["id"]), str(holder.get("label") or "Chat"))
        if not body.get("stream"):
            response = await gw.inference.chat(caller, body)
            _observe(gw, response)
            return response
        # Prepare (authorize, reserve, load) before the 200 is sent so failures are
        # ordinary HTTP errors rather than a truncated stream.
        prepared = await gw.inference.prepare(caller, body)

        async def events() -> AsyncIterator[str]:
            async for event in gw.inference.stream(prepared):
                if event["type"] == "done":
                    _observe(gw, event["response"])
                yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @app.get(f"{PREFIX}/metrics", dependencies=auth)
    async def metrics() -> PlainTextResponse:
        await gw.update_gauges()
        return PlainTextResponse(generate_latest(gw.metrics.registry), media_type=CONTENT_TYPE)

    return app


def _observe(gw: Gateway, response: dict[str, Any]) -> None:
    provider = response.get("provider", "local")
    gw.metrics.requests.labels(provider, "ok").inc()
    gw.metrics.latency.labels(provider).observe((response.get("latencyMs") or 0) / 1000)
    usage = response.get("usage") or {}
    gw.metrics.tokens.labels(provider, "input").inc(usage.get("inputTokens", 0))
    gw.metrics.tokens.labels(provider, "output").inc(usage.get("outputTokens", 0))


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
