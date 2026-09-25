"""Knowledge metrics: retrieval latency, ingestion outcomes, and documents by state."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

METRICS = "/internal/v1/metrics"


def _value(text: str, prefix: str) -> float:
    return sum(
        float(line.rsplit(" ", 1)[1]) for line in text.splitlines() if line.startswith(prefix)
    )


async def test_metrics_cover_ingestion_and_retrieval(
    knowledge: Any, owner_id: uuid.UUID, make_pdf: Callable[[list[str]], bytes]
) -> None:
    kb = await knowledge.create_kb(owner_id)
    await knowledge.upload(kb, "policy.md", b"# Refunds\nRefunds are prorated.")
    await knowledge.upload(kb, "scan.pdf", make_pdf([""]))
    await knowledge.drain()
    await knowledge.query(kb, "refunds")

    resp = await knowledge.client.get(METRICS)
    assert resp.status_code == 200
    text = resp.text
    assert _value(text, 'cq_knowledge_ingests_total{outcome="ready"}') == 1
    assert _value(text, 'cq_knowledge_ingests_total{outcome="failed"}') == 1
    assert _value(text, 'cq_knowledge_documents{state="READY"}') == 1
    assert _value(text, 'cq_knowledge_documents{state="FAILED"}') == 1
    assert _value(text, "cq_knowledge_chunks") == 1
    assert _value(text, "cq_knowledge_retrieval_duration_seconds_count") == 1
    assert 'route="/internal/v1/knowledge-bases/{kb_id}/query"' in text
    assert kb not in text and "policy.md" not in text and "refunds" not in text


async def test_metrics_need_the_service_token(knowledge: Any) -> None:
    resp = await knowledge.client.get(METRICS, headers={"authorization": "Bearer nope"})
    assert resp.status_code == 401
