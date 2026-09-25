"""Owner-triggered backups through the job queue (PLAN.md sections 13.13 and 15.2).

``POST /api/v1/system/backups`` enqueues one ``system.backup`` job (at most one is live:
the dedupe key is constant). The control API process runs a small worker that claims only
that job type, writes the archive into ``CQ_BACKUP_DIR`` and applies the retention
(``CQ_BACKUP_RETENTION``). The API path never includes the device master key: the control
API does not have it (only the broker and the model gateway mount it).

A listing merges the queue's ``system.backup`` jobs (queued, running, failed) with the
archives in the directory, which also shows backups made on the device with
``crewquarters backup create``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_shared import audit, backup, jobs
from crewquarters_shared.config import Settings
from crewquarters_shared.db.models import Job
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger(__name__)

JOB_BACKUP = "system.backup"
DEDUPE_KEY = "system.backup"
LIST_LIMIT = 100
_STATUS = {
    "available": "queued",
    "claimed": "running",
    "succeeded": "succeeded",
    "dead": "failed",
    "cancelled": "failed",
}


def new_backup_name(now: datetime | None = None) -> str:
    return backup.backup_name(now or utcnow(), secrets.token_hex(3))


async def enqueue(db: AsyncSession, *, requested_by: str) -> tuple[int | None, str]:
    """Queue a backup. Returns ``(job id, name)``; the id is ``None`` if one is live."""
    name = new_backup_name()
    job_id = await jobs.enqueue(
        db,
        JOB_BACKUP,
        {"name": name, "requestedBy": requested_by},
        dedupe_key=DEDUPE_KEY,
        max_attempts=1,
    )
    return job_id, name


async def live_job(db: AsyncSession) -> Job | None:
    result = await db.scalars(
        select(Job)
        .where(Job.type == JOB_BACKUP, Job.state.in_(("available", "claimed")))
        .order_by(Job.id.desc())
        .limit(1)
    )
    return result.first()


def _from_manifest(item: dict[str, Any], manifest: dict[str, Any] | None) -> None:
    if not manifest:
        return
    item["includesMasterKey"] = bool(manifest.get("includesMasterKey"))
    item["platformVersion"] = manifest.get("platformVersion")
    item["migrationHead"] = manifest.get("migrationHead")
    item["documentCount"] = (manifest.get("documents") or {}).get("count")
    item["sha256"] = (manifest.get("archive") or {}).get("sha256")
    with contextlib.suppress(KeyError, ValueError, TypeError):
        item["createdAt"] = datetime.fromisoformat(str(manifest["createdAt"]))


def _item(name: str, status: str, source: str, created: datetime | None) -> dict[str, Any]:
    return {
        "id": name,
        "status": status,
        "source": source,
        "createdAt": created,
        "finishedAt": None,
        "sizeBytes": None,
        "includesMasterKey": False,
        "platformVersion": None,
        "migrationHead": None,
        "documentCount": None,
        "sha256": None,
        "downloadable": False,
        "error": None,
    }


def _job_item(job: Job) -> dict[str, Any]:
    item = _item(str(job.payload["name"]), _STATUS.get(job.state, "failed"), "api", job.created_at)
    item["finishedAt"] = job.finished_at
    if job.last_error and item["status"] == "failed":
        item["error"] = {
            "code": str(job.last_error.get("code", "BACKUP_FAILED")),
            "message": str(job.last_error.get("message", "The backup failed.")),
        }
    return item


async def list_backups(db: AsyncSession, settings: Settings) -> list[dict[str, Any]]:
    """Newest first. A succeeded job whose archive retention removed is not listed."""
    rows = (
        await db.scalars(
            select(Job).where(Job.type == JOB_BACKUP).order_by(Job.id.desc()).limit(LIST_LIMIT)
        )
    ).all()
    files = (
        await asyncio.to_thread(backup.list_backups, settings.backup_dir)
        if settings.backup_dir
        else []
    )
    by_name = {f.name: f for f in files}
    items: dict[str, dict[str, Any]] = {}
    for job in rows:
        name = job.payload.get("name")
        if not isinstance(name, str) or name in items:
            continue
        item = _job_item(job)
        if item["status"] == "succeeded" and name not in by_name:
            continue
        items[name] = item
    for name, found in by_name.items():
        item = items.get(name) or _item(name, "succeeded", "device", found.modified_at)
        item.update(status="succeeded", sizeBytes=found.size_bytes, error=None)
        item["finishedAt"] = item.get("finishedAt") or found.modified_at
        _from_manifest(item, found.manifest)
        # Readable by the API and without the master key: the only downloadable kind.
        item["downloadable"] = found.manifest is not None and not item["includesMasterKey"]
        items[name] = item
    return sorted(
        items.values(),
        key=lambda i: (i["createdAt"] or datetime.min.replace(tzinfo=UTC), i["id"]),
        reverse=True,
    )


class BackupWorker:
    """Claims ``system.backup`` jobs in the control API process."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        worker_id: str,
        poll_seconds: float = 1.0,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.worker_id = worker_id
        self.poll_seconds = poll_seconds

    async def run_forever(self, stop: asyncio.Event) -> None:
        if self.settings.backup_dir is not None:
            await asyncio.to_thread(backup.clean_stale_work, self.settings.backup_dir)
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:
                log.exception("backup worker error")
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)

    async def run_once(self) -> bool:
        lease = self.settings.job_lease_seconds
        async with self.sessions() as db, db.begin():
            job = await jobs.claim(db, self.worker_id, lease, [JOB_BACKUP])
        if job is None:
            return False
        keep = asyncio.create_task(self._heartbeat(job.id, lease))
        try:
            await self._execute(job)
        finally:
            keep.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keep
        return True

    async def _heartbeat(self, job_id: int, lease: int) -> None:
        while True:
            await asyncio.sleep(max(0.5, lease / 3))
            async with self.sessions() as db, db.begin():
                await jobs.heartbeat(db, job_id, self.worker_id, lease)

    async def _execute(self, job: jobs.ClaimedJob) -> None:
        settings = self.settings
        name = str(job.payload.get("name") or new_backup_name())
        try:
            if settings.backup_dir is None:
                raise backup.BackupError(
                    "BACKUPS_NOT_CONFIGURED", "CQ_BACKUP_DIR is not set on the control API."
                )
            result = await asyncio.to_thread(
                backup.create_backup,
                settings.backup_dir,
                settings.database_url,
                settings.documents_dir,
                tools=backup.PgTools.from_strings(settings.pg_dump, settings.pg_restore),
                platform_version=settings.platform_version,
                name=name,
            )
            removed = await asyncio.to_thread(
                backup.prune, settings.backup_dir, settings.backup_retention
            )
        except Exception as exc:
            if isinstance(exc, backup.BackupError):
                error = {"code": exc.code, "message": exc.message}
            else:
                log.exception("backup failed", extra={"job_id": job.id})
                error = {"code": "BACKUP_FAILED", "message": type(exc).__name__}
            async with self.sessions() as db, db.begin():
                await jobs.fail(db, job.id, self.worker_id, error, retryable=False)
                audit.record(
                    db,
                    action="system.backup_created",
                    actor_type="user",
                    actor_id=job.payload.get("requestedBy"),
                    target_type="backup",
                    target_id=name,
                    outcome="failure",
                    metadata={"error": error["code"]},
                )
            return
        async with self.sessions() as db, db.begin():
            await jobs.complete(db, job.id, self.worker_id)
            audit.record(
                db,
                action="system.backup_created",
                actor_type="user",
                actor_id=job.payload.get("requestedBy"),
                target_type="backup",
                target_id=name,
                metadata={
                    "bytes": result.size_bytes,
                    "documents": result.manifest.documents.get("count", 0),
                    "migrationHead": result.manifest.migration_head,
                    "includesMasterKey": False,
                    "pruned": removed,
                },
            )
        log.info("backup created", extra={"job_id": job.id, "event": "system.backup_created"})
