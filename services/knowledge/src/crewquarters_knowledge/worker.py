"""Knowledge worker: claims ``knowledge.ingest`` and ``knowledge.purge`` jobs.

Runs inside the knowledge service (the only container with the documents mount).
Ingestion leases are heartbeated until ``CQ_INGEST_MAX_SECONDS``; an ingestion that runs
longer is abandoned and its document FAILED, and the heartbeat stops so the lease can
expire. A job that exhausts its retries marks the document FAILED so the owner sees why,
and a periodic sweep fails documents whose job is gone (say, the process died and the
scheduler's reaper marked it dead). Purges overwrite and remove deleted documents' bytes
and retry until they succeed. Ingestion jobs wait while the embedding model is missing.
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
from crewquarters_knowledge.embeddings import Embedder, ModelUnavailable
from crewquarters_knowledge.metrics import KnowledgeMetrics
from crewquarters_shared import jobs

log = logging.getLogger("crewquarters.knowledge.worker")

# Documents that changed more recently than this are never swept.
SWEEP_GRACE_SECONDS = 60.0
# How often a missing embedding model is looked for again.
MODEL_RETRY_SECONDS = 60.0


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
        self.model_error: str | None = None

    async def load_model(self) -> bool:
        """Verify and load the embedding model (at startup, and again while it is missing).
        A missing or altered model is reported, never fatal: readiness fails instead."""
        try:
            await asyncio.to_thread(self.embedder.load)
        except ModelUnavailable as exc:
            self.model_error = str(exc)
            log.error("embedding model unavailable: %s", exc)
            return False
        self.model_error = None
        return True

    async def run_once(self) -> bool:
        lease = self.settings.ingest_lease_seconds
        # Without a model, ingestion would only fail; leave those jobs queued.
        types = (
            [service.INGEST_JOB, service.PURGE_JOB] if self.embedder.ready else [service.PURGE_JOB]
        )
        async with self.sessions() as db, db.begin():
            job = await jobs.claim(db, self.worker_id, lease, types)
        if job is None:
            return False
        if job.type == service.PURGE_JOB:
            await self._purge(job)
        else:
            await self._ingest(job, lease)
        return True

    async def sweep(self, grace_seconds: float = SWEEP_GRACE_SECONDS) -> int:
        async with self.sessions() as db:
            failed = await service.fail_abandoned(db, grace_seconds)
        if failed:
            log.warning("failed %d documents whose ingestion job was gone", failed)
            self.metrics.ingests.labels("abandoned").inc(failed)
        return failed

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
        limit = self.settings.ingest_max_seconds
        beat = asyncio.create_task(self._heartbeat(job.id, lease, time.monotonic() + limit))
        started = time.perf_counter()
        try:
            outcome = await asyncio.wait_for(
                service.ingest(self.sessions, self.settings, self.embedder, document_id), limit
            )
        except TimeoutError:  # permanent: the same document would time out again
            log.error("ingestion timed out", extra={"job_id": job.id})
            async with self.sessions() as db, db.begin():
                await jobs.fail(
                    db, job.id, self.worker_id, {"code": "INGEST_TIMEOUT"}, retryable=False
                )
            await service.mark_failed(
                self.sessions,
                document_id,
                "INGEST_TIMEOUT",
                f"Indexing took longer than {limit:g} seconds. Split the document and try again.",
            )
            self.metrics.ingests.labels("failed").inc()
            return
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

    async def _heartbeat(self, job_id: int, lease: int, deadline: float) -> None:
        """Extend the lease until ``deadline``, then stop: a stuck ingestion must not keep
        its job leased forever."""
        interval = max(1.0, lease / 3)
        while time.monotonic() + interval < deadline:
            await asyncio.sleep(interval)
            async with self.sessions() as db, db.begin():
                await jobs.heartbeat(db, job_id, self.worker_id, lease)

    async def run_forever(self, stop: asyncio.Event) -> None:
        last_sweep = last_load = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            if not self.embedder.ready and now - last_load >= MODEL_RETRY_SECONDS:
                last_load = now
                await self.load_model()
            try:
                if now - last_sweep >= self.settings.ingest_sweep_seconds:
                    last_sweep = now
                    await self.sweep()
                worked = await self.run_once()
            except Exception:
                log.exception("ingest worker loop error")
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.settings.ingest_poll_seconds)
