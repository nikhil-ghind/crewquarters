"""Model residency: leases, admission control, load/unload, idle reaping, crash
reconciliation (PLAN.md sections 3.1, 8.2, 8.3).

State machine (``model_instances.state``, PLAN.md section 8.2 as updated):

    NOT_LOADED -> LOADING -> READY -> DRAINING -> NOT_LOADED
    LOADING -> LOAD_ERROR -> LOADING (retry)       READY -> RUNTIME_ERROR -> LOADING
    DRAINING (idle) -> READY when a new lease arrives; a manual drain blocks new leases.

Rules:
* A lease request for a model that is not resident enqueues exactly one load job
  (dedupe key ``model:{id}:load``); concurrent callers wait on the same instance.
* Admission: ``reserved + new_peak + margin <= max_serving`` and the host must keep
  ``system_reserve`` free; the more conservative of catalog estimate and telemetry wins.
* The reaper never unloads a model with an active lease; idle models unload after
  ``idle_unload_seconds``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_gateway.config import GatewaySettings
from crewquarters_gateway.runtime import ModelRuntime
from crewquarters_shared import audit, jobs
from crewquarters_shared.db.models_gateway import (
    ModelCatalogEntry,
    ModelInstallation,
    ModelInstance,
    ModelLease,
)
from crewquarters_shared.errors import PlatformError, conflict, not_found
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger("crewquarters.gateway.manager")

JOB_LOAD = "model.load"
JOB_UNLOAD = "model.unload"
ADMISSION_LOCK_KEY = 0x4351414D  # "CQAM": serializes lease acquisition/admission
RESIDENT = ("LOADING", "READY", "DRAINING")


@dataclass(frozen=True)
class Endpoint:
    model_id: str
    base_url: str | None  # None means the in-process mock backend
    served_model: str


class ModelManager:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime: ModelRuntime,
        settings: GatewaySettings,
    ) -> None:
        self.sessions = sessions
        self.runtime = runtime
        self.settings = settings
        self.inflight: dict[str, int] = defaultdict(int)

    # --- catalog views -------------------------------------------------------------

    async def _rows(
        self, db: AsyncSession, model_id: str, lock: bool = False
    ) -> tuple[ModelCatalogEntry, ModelInstallation, ModelInstance]:
        entry = await db.get(ModelCatalogEntry, model_id)
        if entry is None:
            raise not_found("Model", model_id)
        installation = await db.get(ModelInstallation, model_id, with_for_update=lock)
        instance = await db.get(
            ModelInstance, model_id, with_for_update=lock, populate_existing=True
        )
        assert installation is not None and instance is not None
        return entry, installation, instance

    async def describe(self, db: AsyncSession, model_id: str) -> dict[str, Any]:
        entry, installation, instance = await self._rows(db, model_id)
        leases = (
            await db.scalars(
                select(ModelLease)
                .where(ModelLease.model_id == model_id, ModelLease.released_at.is_(None))
                .order_by(ModelLease.created_at)
            )
        ).all()
        profile = entry.profile
        memory_state = {
            "NOT_LOADED": "NOT_LOADED",
            "LOADING": "LOADING",
            "READY": "READY",
            "DRAINING": "DRAINING",
            "LOAD_ERROR": "LOAD_ERROR",
            "RUNTIME_ERROR": "ERROR",
        }[instance.state]
        idle_deadline = None
        if instance.state == "READY" and instance.idle_since is not None and not leases:
            idle_deadline = instance.idle_since + timedelta(
                seconds=self.settings.idle_unload_seconds
            )
        return {
            "id": entry.id,
            "displayName": entry.display_name,
            "family": entry.family,
            "backend": entry.backend,
            "downloadState": installation.state,
            "memoryState": memory_state,
            "stage": instance.stage,
            "diskBytes": installation.bytes_total,
            "download": {
                "bytesDone": installation.bytes_done,
                "bytesTotal": installation.bytes_total,
                "currentFile": installation.current_file,
                "revision": installation.revision,
            },
            "expectedMemoryBytes": entry.expected_memory_bytes,
            "reservedBytes": instance.reserved_bytes,
            "contextLimit": entry.context_limit,
            "capabilities": entry.capabilities,
            "validation": (profile.get("validation") or {}).get("status"),
            "license": profile.get("license"),
            "error": instance.error or installation.error,
            "loadStartedAt": instance.load_started_at.isoformat()
            if instance.load_started_at
            else None,
            "readyAt": instance.ready_at.isoformat() if instance.ready_at else None,
            "idleUnloadAt": idle_deadline.isoformat() if idle_deadline else None,
            "activeLeases": [
                {
                    "id": str(lease.id),
                    "holderType": lease.holder_type,
                    "holderId": lease.holder_id,
                    "label": lease.holder_label,
                    "expiresAt": lease.expires_at.isoformat(),
                }
                for lease in leases
            ],
        }

    async def _refresh_downloads(self, model_ids: list[str] | None = None) -> None:
        """Downloads run in the daemon; mirror live progress into ``model_installations``
        whenever someone reads a model that is still downloading."""
        async with self.sessions() as db:
            stmt = select(ModelInstallation.model_id).where(
                ModelInstallation.state == "DOWNLOADING"
            )
            if model_ids is not None:
                stmt = stmt.where(ModelInstallation.model_id.in_(model_ids))
            downloading = (await db.scalars(stmt)).all()
        for model_id in downloading:
            with contextlib.suppress(PlatformError):
                await self.refresh_installation(model_id)

    async def list_models(self) -> list[dict[str, Any]]:
        await self._refresh_downloads()
        async with self.sessions() as db:
            ids = (
                await db.scalars(select(ModelCatalogEntry.id).order_by(ModelCatalogEntry.id))
            ).all()
            return [await self.describe(db, model_id) for model_id in ids]

    async def get_model(self, model_id: str) -> dict[str, Any]:
        await self._refresh_downloads([model_id])
        async with self.sessions() as db:
            return await self.describe(db, model_id)

    async def memory(self) -> dict[str, Any]:
        capacity = await self.runtime.capacity()
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(
                        ModelInstance.model_id, ModelInstance.state, ModelInstance.reserved_bytes
                    ).where(ModelInstance.state.in_(RESIDENT))
                )
            ).all()
        s = self.settings
        return {
            "totalBytes": capacity.get("memory", {}).get("totalBytes"),
            "availableBytes": capacity.get("memory", {}).get("availableBytes"),
            "systemReserveBytes": s.system_reserve_bytes,
            "maxServingBytes": s.max_serving_bytes,
            "safetyMarginBytes": s.load_safety_margin_bytes,
            "reservedBytes": sum(r.reserved_bytes for r in rows),
            "models": [
                {"id": r.model_id, "state": r.state, "reservedBytes": r.reserved_bytes}
                for r in rows
            ],
        }

    # --- installation ---------------------------------------------------------------

    async def install(self, model_id: str, actor: str | None = None) -> dict[str, Any]:
        async with self.sessions() as db, db.begin():
            entry, installation, _ = await self._rows(db, model_id, lock=True)
            license_info = entry.profile.get("license") or {}
            if license_info.get("gated") and installation.license_accepted_at is None:
                raise conflict("LICENSE_ACCEPTANCE_REQUIRED", "Accept the model license first.")
        state = await self.runtime.install(model_id)
        await self.refresh_installation(model_id, state)
        return await self.get_model(model_id)

    async def cancel_install(self, model_id: str, clear: bool) -> dict[str, Any]:
        state = await self.runtime.cancel_install(model_id, clear)
        await self.refresh_installation(model_id, state)
        return await self.get_model(model_id)

    async def delete_files(self, model_id: str, actor: str | None = None) -> dict[str, Any]:
        async with self.sessions() as db:
            _, _, instance = await self._rows(db, model_id)
            if instance.state in RESIDENT:
                raise conflict("MODEL_RESIDENT", "Unload the model before deleting its files.")
        state = await self.runtime.delete_files(model_id)
        await self.refresh_installation(model_id, state)
        return await self.get_model(model_id)

    async def refresh_installation(
        self, model_id: str, files: dict[str, Any] | None = None
    ) -> None:
        if files is None:
            files = (await self.runtime.model_state(model_id))["files"]
        async with self.sessions() as db, db.begin():
            installation = await db.get(ModelInstallation, model_id, with_for_update=True)
            assert installation is not None
            installation.state = files.get("state", installation.state)
            installation.revision = files.get("revision", installation.revision)
            installation.disk_path = files.get("path")
            installation.bytes_done = int(files.get("bytesDone") or 0)
            installation.bytes_total = files.get("bytesTotal")
            installation.current_file = files.get("currentFile")
            installation.checksum = files.get("manifestSha256", installation.checksum)
            installation.error = files.get("error")
            if installation.state == "INSTALLED" and files.get("verifiedAt"):
                installation.last_verified_at = utcnow()

    # --- leases and admission ---------------------------------------------------------

    async def _admit(self, db: AsyncSession, entry: ModelCatalogEntry) -> None:
        s = self.settings
        others = (
            await db.execute(
                select(ModelInstance.model_id, ModelInstance.reserved_bytes).where(
                    ModelInstance.state.in_(RESIDENT), ModelInstance.model_id != entry.id
                )
            )
        ).all()
        peak = entry.expected_memory_bytes
        loaded = [row.model_id for row in others]
        if s.one_generative_model and loaded:
            raise PlatformError(
                "MODEL_CAPACITY_EXCEEDED",
                "Another model is loaded. Unload it before loading this one.",
                409,
                {"loadedModels": loaded, "policy": "one_generative_model"},
            )
        reserved = sum(row.reserved_bytes for row in others)
        if reserved + peak + s.load_safety_margin_bytes > s.max_serving_bytes:
            raise PlatformError(
                "MODEL_CAPACITY_EXCEEDED",
                "Not enough safe unified memory for this model.",
                409,
                {
                    "reservedBytes": reserved,
                    "requiredBytes": peak,
                    "maxServingBytes": s.max_serving_bytes,
                },
            )
        capacity = await self.runtime.capacity()
        available = int(capacity.get("memory", {}).get("availableBytes") or 0)
        if available - s.system_reserve_bytes < peak + s.load_safety_margin_bytes:
            raise PlatformError(
                "MODEL_CAPACITY_EXCEEDED",
                "Not enough free memory on the device right now.",
                409,
                {
                    "availableBytes": available,
                    "requiredBytes": peak,
                    "systemReserveBytes": s.system_reserve_bytes,
                },
            )

    async def acquire(
        self,
        model_id: str,
        holder_type: str,
        holder_id: str,
        label: str,
        ttl_seconds: int,
        manual: bool = False,
    ) -> ModelLease:
        """Create or renew the holder's lease; start a load if the model is not resident.

        All acquisitions serialize on one transaction-level advisory lock, so admission
        control always sees every other model's committed residency."""
        async with self.sessions() as db, db.begin():
            await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADMISSION_LOCK_KEY})
            entry, installation, instance = await self._rows(db, model_id, lock=True)
            if installation.state != "INSTALLED":
                raise conflict("MODEL_NOT_INSTALLED", f"Install {model_id} before using it.")
            if instance.state == "DRAINING" and instance.drain_reason == "manual":
                raise conflict("MODEL_UNLOADING", f"{model_id} is being unloaded.")
            now = utcnow()
            expires = now + timedelta(seconds=ttl_seconds)
            lease_id = await db.scalar(
                pg_insert(ModelLease)
                .values(
                    model_id=model_id,
                    holder_type=holder_type,
                    holder_id=holder_id,
                    holder_label=label,
                    expires_at=expires,
                )
                .on_conflict_do_update(
                    index_elements=["model_id", "holder_type", "holder_id"],
                    index_where=ModelLease.released_at.is_(None),
                    set_={"expires_at": expires, "holder_label": label},
                )
                .returning(ModelLease.id)
            )
            if instance.state in ("NOT_LOADED", "LOAD_ERROR", "RUNTIME_ERROR"):
                await self._admit(db, entry)
                instance.state = "LOADING"
                instance.stage = "Queued"
                instance.error = None
                instance.reserved_bytes = entry.expected_memory_bytes
                instance.load_started_at = now
                instance.ready_at = None
                instance.version += 1
                await jobs.enqueue(
                    db,
                    JOB_LOAD,
                    {"modelId": model_id},
                    dedupe_key=f"model:{model_id}:load",
                    max_attempts=1,
                )
                audit.record(
                    db,
                    action="model.load",
                    actor_type="system",
                    target_type="model",
                    target_id=model_id,
                    metadata={"holderType": holder_type, "holder": label, "manual": manual},
                )
            elif instance.state == "DRAINING" and instance.drain_reason == "idle":
                # Idle drain not started yet: keep the model resident.
                instance.state = "READY"
                instance.drain_reason = None
                instance.stage = None
            # DRAINING/"stopping": the container is being stopped; the unload job reloads
            # the model because this lease exists.
            instance.idle_since = None
            lease = await db.get(ModelLease, lease_id)
            assert lease is not None
            return lease

    async def release(self, lease_id: Any, reason: str = "released") -> None:
        async with self.sessions() as db, db.begin():
            lease = await db.get(ModelLease, lease_id, with_for_update=True)
            if lease is None or lease.released_at is not None:
                return
            lease.released_at = utcnow()
            lease.release_reason = reason
            await self._mark_idle_if_unused(db, lease.model_id)

    async def release_holder(self, holder_type: str, holder_id: str, reason: str) -> int:
        async with self.sessions() as db, db.begin():
            leases = (
                await db.scalars(
                    select(ModelLease)
                    .where(
                        ModelLease.holder_type == holder_type,
                        ModelLease.holder_id == holder_id,
                        ModelLease.released_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
            for lease in leases:
                lease.released_at = utcnow()
                lease.release_reason = reason
            for model_id in {lease.model_id for lease in leases}:
                await self._mark_idle_if_unused(db, model_id)
            return len(leases)

    async def _mark_idle_if_unused(self, db: AsyncSession, model_id: str) -> None:
        await db.flush()
        active = await db.scalar(
            select(func.count())
            .select_from(ModelLease)
            .where(ModelLease.model_id == model_id, ModelLease.released_at.is_(None))
        )
        if not active:
            await db.execute(
                update(ModelInstance)
                .where(
                    ModelInstance.model_id == model_id,
                    ModelInstance.state == "READY",
                    ModelInstance.idle_since.is_(None),
                )
                .values(idle_since=utcnow())
            )

    async def wait_ready(self, model_id: str, limit_seconds: float | None = None) -> Endpoint:
        deadline = asyncio.get_running_loop().time() + (
            limit_seconds or self.settings.wait_ready_seconds
        )
        while True:
            async with self.sessions() as db:
                instance = await db.get(ModelInstance, model_id)
            assert instance is not None
            if instance.state == "READY" and instance.endpoint:
                return self._endpoint(model_id, instance.endpoint)
            if instance.state in ("LOAD_ERROR", "RUNTIME_ERROR"):
                raise PlatformError(
                    "MODEL_LOAD_FAILED",
                    f"{model_id} failed to load.",
                    503,
                    {"error": instance.error},
                )
            if instance.state == "NOT_LOADED" or (
                instance.state == "DRAINING" and instance.drain_reason == "manual"
            ):
                # "stopping" is not listed: the unload job will reload for waiting leases.
                raise PlatformError("MODEL_UNAVAILABLE", f"{model_id} is not loaded.", 503)
            if asyncio.get_running_loop().time() > deadline:
                raise PlatformError(
                    "MODEL_LOAD_TIMEOUT", f"{model_id} did not become ready in time.", 504
                )
            await asyncio.sleep(self.settings.load_poll_seconds / 2)

    def _endpoint(self, model_id: str, endpoint: dict[str, Any]) -> Endpoint:
        if endpoint.get("inprocess"):
            return Endpoint(model_id, None, endpoint.get("servedModelName", model_id))
        host = (
            endpoint.get("ip") if self.settings.model_addressing == "ip" else endpoint.get("name")
        )
        return Endpoint(model_id, f"http://{host}:{endpoint['port']}", endpoint["servedModelName"])

    # --- manual load/unload -------------------------------------------------------------

    async def manual_load(self, model_id: str, actor: str) -> dict[str, Any]:
        await self.acquire(
            model_id,
            "manual",
            actor,
            "Manual load",
            self.settings.manual_lease_ttl_seconds,
            manual=True,
        )
        # A manual load is a one-off: release the lease so the idle timer governs unload.
        await self.release_holder("manual", actor, "manual_load_started")
        return await self.get_model(model_id)

    async def unload(self, model_id: str, force: bool, actor: str | None) -> dict[str, Any]:
        async with self.sessions() as db, db.begin():
            _, _, instance = await self._rows(db, model_id, lock=True)
            if instance.state not in RESIDENT:
                return await self.describe(db, model_id)
            leases = (
                await db.scalars(
                    select(ModelLease)
                    .where(ModelLease.model_id == model_id, ModelLease.released_at.is_(None))
                    .with_for_update()
                )
            ).all()
            if leases and not force:
                raise PlatformError(
                    "MODEL_IN_USE",
                    "The model is in use. Confirm to unload anyway.",
                    409,
                    {"holders": [lease.holder_label for lease in leases]},
                )
            for lease in leases:
                lease.released_at = utcnow()
                lease.release_reason = "forced_unload"
            instance.state = "DRAINING"
            instance.drain_reason = "manual"
            instance.stage = "Draining"
            instance.version += 1
            await jobs.enqueue(
                db,
                JOB_UNLOAD,
                {"modelId": model_id},
                dedupe_key=f"model:{model_id}:unload",
                max_attempts=3,
            )
            return await self.describe(db, model_id)

    # --- job handlers -------------------------------------------------------------------

    async def _set(self, model_id: str, **fields: Any) -> None:
        async with self.sessions() as db, db.begin():
            await db.execute(
                update(ModelInstance).where(ModelInstance.model_id == model_id).values(**fields)
            )

    async def handle_load(self, payload: dict[str, Any]) -> None:
        model_id = payload["modelId"]
        async with self.sessions() as db:
            entry, _, instance = await self._rows(db, model_id)
        if instance.state != "LOADING":
            return
        profile = entry.profile
        timeout = float((profile.get("launch") or {}).get("startupTimeoutSeconds", 600))
        try:
            await self._set(model_id, stage="Pulling image")
            await self.runtime.prepare(model_id)
            await self._set(model_id, stage="Starting container")
            state = await self.runtime.start(model_id)
            await self._set(model_id, stage="Loading weights")
            endpoint_info = (state.get("container") or {}).get("endpoint") or {}
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                state = await self.runtime.model_state(model_id)
                container = state.get("container") or {}
                if container.get("state") in ("exited", "dead", "absent"):
                    raise PlatformError(
                        "MODEL_START_FAILED",
                        "The model server exited during startup.",
                        502,
                        {
                            "exitCode": container.get("exitCode"),
                            "oomKilled": container.get("oomKilled"),
                        },
                    )
                endpoint_info = container.get("endpoint") or endpoint_info
                if await self._healthy(model_id, endpoint_info):
                    break
                if asyncio.get_running_loop().time() > deadline:
                    raise PlatformError(
                        "MODEL_LOAD_TIMEOUT", "The model server did not become healthy.", 504
                    )
                await self._set(model_id, stage="Health check")
                await asyncio.sleep(self.settings.load_poll_seconds)
        except PlatformError as exc:
            await self._fail_load(model_id, exc.code, exc.message, exc.details)
            return
        except Exception as exc:  # never leave the instance stuck in LOADING
            log.exception("model load crashed", extra={"event": "model.load_crashed"})
            await self._fail_load(model_id, "MODEL_START_FAILED", type(exc).__name__, {})
            return
        async with self.sessions() as db, db.begin():
            _, _, instance = await self._rows(db, model_id, lock=True)
            if instance.state != "LOADING":  # unloaded/forced meanwhile
                return
            active = await db.scalar(
                select(func.count())
                .select_from(ModelLease)
                .where(ModelLease.model_id == model_id, ModelLease.released_at.is_(None))
            )
            instance.state = "READY"
            instance.stage = None
            instance.endpoint = endpoint_info
            instance.ready_at = utcnow()
            instance.idle_since = None if active else utcnow()
            instance.version += 1
        log.info("model ready", extra={"event": "model.ready", "model": model_id})

    async def _healthy(self, model_id: str, endpoint: dict[str, Any]) -> bool:
        """Ready only when the server answers and serves the expected model name."""
        if not endpoint:
            return False
        if endpoint.get("inprocess"):
            return True
        target = self._endpoint(model_id, endpoint)
        path = endpoint.get("healthPath", "/v1/models")
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{target.base_url}{path}")
            if response.status_code != 200:
                return False
            served = {m.get("id") for m in response.json().get("data", [])}
            return target.served_model in served
        except (httpx.HTTPError, ValueError):
            return False

    async def _fail_load(
        self, model_id: str, code: str, message: str, details: dict[str, Any]
    ) -> None:
        with contextlib.suppress(PlatformError):
            await self.runtime.stop(model_id)
        async with self.sessions() as db, db.begin():
            _, _, instance = await self._rows(db, model_id, lock=True)
            if instance.state != "LOADING":
                return
            instance.state = "LOAD_ERROR"
            instance.stage = None
            instance.error = {"code": code, "message": message, **details}
            instance.reserved_bytes = 0
            instance.endpoint = None
            instance.version += 1
            audit.record(
                db,
                action="model.load_failed",
                actor_type="system",
                target_type="model",
                target_id=model_id,
                outcome="failure",
                metadata={"code": code},
            )

    async def handle_unload(self, payload: dict[str, Any]) -> None:
        model_id = payload["modelId"]
        async with self.sessions() as db, db.begin():
            _, _, instance = await self._rows(db, model_id, lock=True)
            if instance.state != "DRAINING":
                return
            active = await self._active_leases(db, model_id)
            if instance.drain_reason == "idle" and active:
                instance.state = "READY"
                instance.drain_reason = None
                instance.stage = None
                return
            manual = instance.drain_reason == "manual"
            if not manual:
                # Point of no return: a lease arriving now waits for a reload instead of
                # cancelling the drain (the container is about to stop).
                instance.drain_reason = "stopping"
                instance.stage = "Stopping"
        if manual:  # let in-flight requests finish, bounded
            deadline = asyncio.get_running_loop().time() + self.settings.manual_drain_seconds
            # Bounded poll of this process's in-flight counter (no event to await).
            while self.inflight[model_id] and asyncio.get_running_loop().time() < deadline:  # noqa: ASYNC110
                await asyncio.sleep(0.2)
        report = await self.runtime.stop(model_id)
        removed = bool(report.get("containerRemoved"))
        async with self.sessions() as db, db.begin():
            entry, _, instance = await self._rows(db, model_id, lock=True)
            if instance.state != "DRAINING":
                return
            active = await self._active_leases(db, model_id)
            instance.drain_reason = None
            instance.endpoint = None
            instance.idle_since = None
            instance.observed = report
            instance.version += 1
            if removed and active:
                # A lease arrived while stopping: load again (the reservation was kept).
                instance.state = "LOADING"
                instance.stage = "Queued"
                instance.error = None
                instance.reserved_bytes = entry.expected_memory_bytes
                instance.load_started_at = utcnow()
                await jobs.enqueue(
                    db,
                    JOB_LOAD,
                    {"modelId": model_id},
                    dedupe_key=f"model:{model_id}:load",
                    max_attempts=1,
                )
                return
            instance.state = "NOT_LOADED" if removed else "RUNTIME_ERROR"
            instance.stage = None
            instance.reserved_bytes = 0
            instance.error = (
                None
                if removed
                else {"code": "UNLOAD_UNVERIFIED", "message": "Container still present."}
            )
            audit.record(
                db,
                action="model.unloaded",
                actor_type="system",
                target_type="model",
                target_id=model_id,
                metadata={"verified": removed},
            )

    async def _active_leases(self, db: AsyncSession, model_id: str) -> int:
        await db.flush()
        return int(
            await db.scalar(
                select(func.count())
                .select_from(ModelLease)
                .where(ModelLease.model_id == model_id, ModelLease.released_at.is_(None))
            )
            or 0
        )

    async def _live_job(self, dedupe_key: str) -> bool:
        async with self.sessions() as db:
            found = await db.scalar(
                text(
                    "SELECT 1 FROM jobs WHERE dedupe_key = :k AND state IN ('available', 'claimed')"
                ),
                {"k": dedupe_key},
            )
        return found is not None

    # --- reaper -------------------------------------------------------------------------

    async def reap(self, run_is_active: Any = None) -> dict[str, int]:
        """Expire leases, release leases of finished runs, unload idle models, and mark
        crashed servers. Called periodically by the elected gateway leader."""
        report = {"expired": 0, "runReleased": 0, "idleUnloads": 0, "crashed": 0}
        now = utcnow()
        async with self.sessions() as db, db.begin():
            expired = (
                await db.scalars(
                    select(ModelLease)
                    .where(ModelLease.released_at.is_(None), ModelLease.expires_at <= now)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for lease in expired:
                lease.released_at = now
                lease.release_reason = "expired"
            report["expired"] = len(expired)
            for model_id in {lease.model_id for lease in expired}:
                await self._mark_idle_if_unused(db, model_id)
        if run_is_active is not None:
            async with self.sessions() as db:
                run_leases = (
                    await db.scalars(
                        select(ModelLease).where(
                            ModelLease.holder_type == "run", ModelLease.released_at.is_(None)
                        )
                    )
                ).all()
            for lease in run_leases:
                if not await run_is_active(lease.holder_id):
                    report["runReleased"] += await self.release_holder(
                        "run", lease.holder_id, "run_finished"
                    )
        async with self.sessions() as db, db.begin():
            instances = (
                await db.scalars(
                    select(ModelInstance)
                    .where(ModelInstance.state.in_(("READY", "DRAINING")))
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for instance in instances:
                active = await db.scalar(
                    select(func.count())
                    .select_from(ModelLease)
                    .where(
                        ModelLease.model_id == instance.model_id, ModelLease.released_at.is_(None)
                    )
                )
                if instance.state == "READY" and not active:
                    if instance.idle_since is None:
                        instance.idle_since = now
                    elif (
                        now - instance.idle_since
                    ).total_seconds() >= self.settings.idle_unload_seconds:
                        instance.state = "DRAINING"
                        instance.drain_reason = "idle"
                        instance.stage = "Unloading (idle)"
                        instance.version += 1
                        await jobs.enqueue(
                            db,
                            JOB_UNLOAD,
                            {"modelId": instance.model_id},
                            dedupe_key=f"model:{instance.model_id}:unload",
                            max_attempts=3,
                        )
                        report["idleUnloads"] += 1
        async with self.sessions() as db:
            ready = (
                await db.scalars(
                    select(ModelInstance.model_id).where(ModelInstance.state == "READY")
                )
            ).all()
        for model_id in ready:
            state = await self.runtime.model_state(model_id)
            container = state.get("container") or {}
            if container.get("state") != "running":
                async with self.sessions() as db, db.begin():
                    _, _, instance = await self._rows(db, model_id, lock=True)
                    if instance.state != "READY":
                        continue
                    instance.state = "RUNTIME_ERROR"
                    instance.error = {
                        "code": "MODEL_CRASHED",
                        "message": "The model server stopped unexpectedly.",
                        "exitCode": container.get("exitCode"),
                        "oomKilled": container.get("oomKilled"),
                    }
                    instance.reserved_bytes = 0
                    instance.endpoint = None
                    instance.version += 1
                    audit.record(
                        db,
                        action="model.crashed",
                        actor_type="system",
                        target_type="model",
                        target_id=model_id,
                        outcome="failure",
                        metadata=instance.error,
                    )
                report["crashed"] += 1
                with contextlib.suppress(PlatformError):
                    await self.runtime.stop(model_id)
        report["recovered"] = await self._recover_stuck()
        report["orphansStopped"] = await self._stop_orphans()
        await self._refresh_downloads()
        return report

    async def _recover_stuck(self) -> int:
        """LOADING or DRAINING with no live job (gateway restart, dead job): adopt a
        healthy server, re-enqueue the unload, or fail the load so memory is released."""
        recovered = 0
        grace = timedelta(seconds=max(30.0, self.settings.load_poll_seconds * 10))
        async with self.sessions() as db:
            stuck = (
                await db.scalars(
                    select(ModelInstance).where(ModelInstance.state.in_(("LOADING", "DRAINING")))
                )
            ).all()
        for instance in stuck:
            if instance.state == "LOADING":
                if await self._live_job(f"model:{instance.model_id}:load"):
                    continue
                if instance.load_started_at and utcnow() - instance.load_started_at < grace:
                    continue
                state = await self.runtime.model_state(instance.model_id)
                container = state.get("container") or {}
                endpoint = container.get("endpoint") or {}
                if container.get("state") == "running" and await self._healthy(
                    instance.model_id, endpoint
                ):
                    async with self.sessions() as db, db.begin():
                        _, _, row = await self._rows(db, instance.model_id, lock=True)
                        if row.state == "LOADING":
                            row.state, row.stage, row.endpoint = "READY", None, endpoint
                            row.ready_at = utcnow()
                            row.version += 1
                else:
                    await self._fail_load(
                        instance.model_id,
                        "LOAD_INTERRUPTED",
                        "The load was interrupted; retry.",
                        {},
                    )
                recovered += 1
            elif not await self._live_job(f"model:{instance.model_id}:unload"):
                async with self.sessions() as db, db.begin():
                    await jobs.enqueue(
                        db,
                        JOB_UNLOAD,
                        {"modelId": instance.model_id},
                        dedupe_key=f"model:{instance.model_id}:unload",
                        max_attempts=3,
                    )
                recovered += 1
        return recovered

    async def _stop_orphans(self) -> int:
        """A serving container for a model the gateway does not consider resident (for
        example, a start that completed after a timed-out load) is stopped."""
        stopped = 0
        async with self.sessions() as db:
            idle = (
                await db.scalars(
                    select(ModelInstance.model_id).where(
                        ModelInstance.state.in_(("NOT_LOADED", "LOAD_ERROR", "RUNTIME_ERROR"))
                    )
                )
            ).all()
        for model_id in idle:
            state = await self.runtime.model_state(model_id)
            if (state.get("container") or {}).get("state") not in (None, "absent"):
                with contextlib.suppress(PlatformError):
                    await self.runtime.stop(model_id)
                    stopped += 1
        return stopped
