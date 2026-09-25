"""Knowledge worker: claims ``knowledge.ingest`` and ``knowledge.purge`` jobs.

Runs inside the knowledge service (the only container with the documents mount).
Ingestion leases are heartbeated; a job that exhausts its retries marks the document
FAILED so the owner sees why. Purges overwrite and remove deleted documents' bytes and
retry until they succeed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_knowledge import service
from crewquarters_knowledge.config import KnowledgeSettings
from crewquarters_knowledge.embeddings import Embedder
from crewquarters_knowledge.metrics import KnowledgeMetrics
from crewquarters_shared import jobs

log = logging.getLogger("crewquarters.knowledge.worker")


class KnowledgeWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: KnowledgeSettings,
        embedder: Embedder,
        metrics: KnowledgeMetrics,
        worker_id: str | None = None,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.embedder = embedder
        self.metrics = metrics
        self.worker_id = worker_id or f"knowledge-{uuid.uuid4().hex[:8]}"

    async def run_once(self) -> bool:
        lease = self.settings.ingest_lease_seconds
        async with self.sessions() as db, db.begin():
            job = await jobs.claim(
                db, self.worker_id, lease, [service.INGEST_JOB, service.PURGE_JOB]
            )
        if job is None:
            return False
        if job.type == service.PURGE_JOB:
            await self._purge(job)
        else:
            await self._ingest(job, lease)
        return True

    async def _purge(self, job: jobs.ClaimedJob) -> None:
        try:
            await asyncio.to_thread(service.purge, self.settings, str(job.payload["path"]))
        except ValueError:  # never delete outside the documents directory
            log.error("refused purge outside the documents directory", extra={"job_id": job.id})
            outcome = "refused"
        except OSError as exc:
            log.warning("purge failed; will retry", extra={"job_id": job.id})
            async with self.sessions() as db, db.begin():
                state = await jobs.fail(
                    db,
                    job.id,
                    self.worker_id,
                    {"code": "PURGE_FAILED", "type": type(exc).__name__},
                    retryable=True,
                )
            self.metrics.purges.labels("dead" if state == "dead" else "retry").inc()
            return
        else:
            outcome = "done"
        async with self.sessions() as db, db.begin():
            await jobs.complete(db, job.id, self.worker_id)
        self.metrics.purges.labels(outcome).inc()

    async def _ingest(self, job: jobs.ClaimedJob, lease: int) -> None:
        document_id = uuid.UUID(job.payload["documentId"])
        beat = asyncio.create_task(self._heartbeat(job.id, lease))
        started = time.perf_counter()
        try:
            outcome = await service.ingest(self.sessions, self.settings, self.embedder, document_id)
        except Exception as exc:  # retried with backoff; the error class is recorded
            log.exception("ingestion failed", extra={"job_id": job.id})
            async with self.sessions() as db, db.begin():
                state = await jobs.fail(
                    db,
                    job.id,
                    self.worker_id,
                    {"code": "INGEST_FAILED", "type": type(exc).__name__},
                    retryable=True,
                )
            self.metrics.ingests.labels("dead" if state == "dead" else "retry").inc()
            if state == "dead":
                await service.mark_failed(
                    self.sessions,
                    document_id,
                    "INGEST_FAILED",
                    "Indexing failed repeatedly. Re-index to try again.",
                )
            elif state == "available":
                await service.mark_failed(
                    self.sessions,
                    document_id,
                    "INGEST_RETRYING",
                    "Indexing will be retried.",
                    state="PENDING",
                )
            return
        finally:
            self.metrics.ingest_duration.observe(time.perf_counter() - started)
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        async with self.sessions() as db, db.begin():
            await jobs.complete(db, job.id, self.worker_id)
        self.metrics.ingests.labels(outcome).inc()

    async def _heartbeat(self, job_id: int, lease: int) -> None:
        while True:
            await asyncio.sleep(max(1.0, lease / 3))
            async with self.sessions() as db, db.begin():
                await jobs.heartbeat(db, job_id, self.worker_id, lease)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:
                log.exception("ingest worker loop error")
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.settings.ingest_poll_seconds)
