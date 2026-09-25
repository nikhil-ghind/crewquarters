"""Knowledge metrics (PLAN.md section 15.1), served at ``/internal/v1/metrics``.

Labels never carry document names, IDs, or query text.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_knowledge.models import Document, DocumentChunk


class KnowledgeMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "cq_http_requests_total",
            "HTTP requests by route template, method, and status.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "cq_http_request_duration_seconds",
            "HTTP request latency.",
            ["method", "route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2.5, 5),
            registry=self.registry,
        )
        self.retrieval = Histogram(
            "cq_knowledge_retrieval_duration_seconds",
            "Query embedding plus vector search, excluding generation (target p95 < 1 s).",
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
            registry=self.registry,
        )
        self.ingests = Counter(
            "cq_knowledge_ingests_total",
            "Ingestion attempts by outcome: ready, failed, missing, retry, dead.",
            ["outcome"],
            registry=self.registry,
        )
        self.ingest_duration = Histogram(
            "cq_knowledge_ingest_duration_seconds",
            "Time to extract, chunk, embed, and index one document.",
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
            registry=self.registry,
        )
        self.purges = Counter(
            "cq_knowledge_purges_total",
            "Document-file purges by outcome: done, retry, dead, refused.",
            ["outcome"],
            registry=self.registry,
        )
        self.documents = Gauge(
            "cq_knowledge_documents", "Documents by state.", ["state"], registry=self.registry
        )
        self.chunks = Gauge(
            "cq_knowledge_chunks",
            "Indexed chunks across all knowledge bases.",
            registry=self.registry,
        )

    async def collect_database(self, db: AsyncSession) -> None:
        self.documents.clear()
        rows = await db.execute(select(Document.state, func.count()).group_by(Document.state))
        for state, count in rows.all():
            self.documents.labels(state).set(count)
        self.chunks.set(
            float(await db.scalar(select(func.count()).select_from(DocumentChunk)) or 0)
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
