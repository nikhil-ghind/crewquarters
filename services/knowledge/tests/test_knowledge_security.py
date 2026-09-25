"""Security review regressions (docs/security-review-person3.md)."""

from __future__ import annotations

import stat
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from crewquarters_knowledge import service


async def test_oversized_upload_is_refused_before_spooling(
    knowledge: Any, owner_id: uuid.UUID
) -> None:
    kb = await knowledge.create_kb(owner_id)
    knowledge.settings.max_upload_bytes = 1000
    resp = await knowledge.upload(kb, "big.txt", b"x" * 200_000)
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    staging = knowledge.settings.documents_dir / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


async def test_other_routes_keep_the_small_limit(knowledge: Any, owner_id: uuid.UUID) -> None:
    kb = await knowledge.create_kb(owner_id)
    query = "q" * (knowledge.settings.max_body_bytes + 1)
    resp = await knowledge.client.post(
        f"/internal/v1/knowledge-bases/{kb}/query", json={"query": query}
    )
    assert resp.status_code == 413


async def test_stored_documents_are_private(knowledge: Any, owner_id: uuid.UUID) -> None:
    kb = await knowledge.create_kb(owner_id)
    await knowledge.upload(kb, "a.txt", b"personal notes")
    [stored] = [p for p in (knowledge.settings.documents_dir / kb).iterdir() if p.is_file()]
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert stat.S_IMODE((knowledge.settings.documents_dir / kb).stat().st_mode) == 0o700


async def test_slow_extraction_times_out(
    knowledge: Any, owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stuck(path: Path, mime: str) -> list[Any]:
        time.sleep(1.0)
        return []

    monkeypatch.setattr(service, "extract", stuck)
    knowledge.settings.extract_timeout_seconds = 0.1
    kb = await knowledge.create_kb(owner_id)
    resp = await knowledge.upload(kb, "a.txt", b"text")
    await knowledge.drain()
    doc = (await knowledge.client.get(f"/internal/v1/documents/{resp.json()['id']}")).json()
    assert doc["state"] == "FAILED" and doc["error"]["code"] == "EXTRACTION_TIMEOUT"


async def test_ingest_failures_do_not_log_document_text(
    knowledge: Any, owner_id: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    def leaky(texts: list[str]) -> list[list[float]]:
        raise RuntimeError(f"insert failed with parameters {texts}")

    knowledge.app.state.worker.embedder.embed_documents = leaky
    kb = await knowledge.create_kb(owner_id)
    await knowledge.upload(kb, "a.txt", b"Patient record: highly private words")
    with caplog.at_level("DEBUG"):
        await knowledge.app.state.worker.run_once()
    assert "ingestion failed" in caplog.text
    assert "highly private" not in caplog.text


async def test_no_unauthenticated_api_map(knowledge: Any) -> None:
    anonymous = {"authorization": ""}
    for path in ("/openapi.json", "/docs", "/internal/v1/knowledge-bases"):
        resp = await knowledge.client.get(path, headers=anonymous)
        assert resp.status_code in (401, 404), path
