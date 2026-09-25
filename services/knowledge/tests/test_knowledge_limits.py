"""Bounded ingestion: extraction in a limited child process, ingestion deadlines, the
abandoned-document sweep, and a missing embedding model."""

from __future__ import annotations

import asyncio
import io
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_knowledge import extract as ex
from crewquarters_knowledge import isolation
from crewquarters_knowledge.config import KnowledgeSettings
from crewquarters_knowledge.embeddings import HashingEmbedder, ModelUnavailable
from crewquarters_knowledge.extract import SUPPORTED, ExtractionError
from crewquarters_knowledge.main import create_app
from crewquarters_knowledge.models import Document
from crewquarters_shared.config import Settings
from crewquarters_shared.db.models import Job

DOCX = SUPPORTED[".docx"]
GENEROUS = isolation.Limits(timeout_seconds=60, memory_bytes=1024**3)


def inflated_docx(make_docx: Callable[..., bytes], paragraphs: int) -> bytes:
    """A small .docx whose word/document.xml inflates to ``paragraphs`` tiny paragraphs."""
    source = zipfile.ZipFile(io.BytesIO(make_docx("Title", ["x"])))
    xml = source.read("word/document.xml").decode()
    head, _, rest = xml.partition("<w:body>")
    body = "<w:p><w:r><w:t>a</w:t></w:r></w:p>" * paragraphs
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/document.xml":
                data = f"{head}<w:body>{rest.replace('<w:sectPr', body + '<w:sectPr', 1)}".encode()
            target.writestr(item.filename, data)
    return out.getvalue()


async def _prepare(path: Path, mime: str, limits: isolation.Limits) -> isolation.Prepared:
    return await isolation.prepare(path, mime, 800, 120, limits)


async def test_extraction_runs_in_a_child_process(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# Refunds\n\nRefunds are available within 30 days.\n")
    prepared = await _prepare(path, "text/markdown", GENEROUS)
    assert prepared.segments == 2 and prepared.tokens > 5
    assert [p.locator["section"] for p in prepared.pieces] == ["Refunds"]


async def test_extraction_errors_come_back_from_the_child(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ExtractionError) as failed:
        await _prepare(path, "text/plain", GENEROUS)
    assert failed.value.code == "UNSUPPORTED_ENCODING"


async def test_extraction_has_a_wall_clock_timeout(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("hello")
    started = time.monotonic()
    with pytest.raises(ExtractionError) as failed:
        await _prepare(path, "text/plain", isolation.Limits(0.01, 1024**3))
    assert failed.value.code == "EXTRACTION_TIMEOUT"
    assert time.monotonic() - started < 5


async def test_extraction_has_a_memory_limit(tmp_path: Path) -> None:
    path = tmp_path / "big.txt"
    path.write_text(("word " * 20 + "\n") * 60_000)  # 6 MB of text, ~1.2 M tokens
    roomy = isolation.Limits(60, 1024**3, max_chars=10**8, max_chunks=10**6)
    assert (await _prepare(path, "text/plain", roomy)).pieces
    tight = isolation.Limits(60, 128 * 1024**2, max_chars=10**8, max_chunks=10**6)
    with pytest.raises(ExtractionError) as failed:
        await _prepare(path, "text/plain", tight)
    assert failed.value.code == "DOCUMENT_TOO_COMPLEX"


async def test_extracted_characters_and_chunks_are_capped(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text(("word " * 200 + "\n\n") * 20)
    for limits in (
        isolation.Limits(60, 1024**3, max_chars=1000),
        isolation.Limits(60, 1024**3, max_chunks=2),
    ):
        with pytest.raises(ExtractionError) as failed:
            await _prepare(path, "text/plain", limits)
        assert failed.value.code == "DOCUMENT_TOO_COMPLEX"
    assert len((await _prepare(path, "text/plain", GENEROUS)).pieces) > 2


def test_docx_body_size_and_paragraph_count_are_capped(
    tmp_path: Path, make_docx: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bomb.docx"
    path.write_bytes(inflated_docx(make_docx, 150_000))  # ~5 MB of XML in ~50 KB
    assert path.stat().st_size < 100_000
    started = time.monotonic()
    with pytest.raises(ExtractionError) as failed:
        ex.extract(path, DOCX)
    assert failed.value.code == "DOCUMENT_TOO_COMPLEX"
    assert time.monotonic() - started < 2  # refused before parsing

    path.write_bytes(inflated_docx(make_docx, 3_000))
    monkeypatch.setattr(ex, "MAX_DOCX_BLOCKS", 2_000)
    with pytest.raises(ExtractionError) as failed:
        ex.extract(path, DOCX)
    assert failed.value.code == "DOCUMENT_TOO_COMPLEX"


def test_many_paragraphs_extract_quickly(tmp_path: Path, make_docx: Callable[..., bytes]) -> None:
    path = tmp_path / "long.docx"
    path.write_bytes(inflated_docx(make_docx, 30_000))
    started = time.monotonic()
    segments = ex.extract(path, DOCX)
    assert len(segments) == 30_002  # the title, "x", and 30,000 paragraphs
    assert segments[0].locator == {"paragraph": 1, "section": "Title"}
    assert time.monotonic() - started < 10


async def test_a_docx_bomb_fails_the_document(
    knowledge: Any, owner_id: uuid.UUID, make_docx: Callable[..., bytes]
) -> None:
    kb = await knowledge.create_kb(owner_id)
    resp = await knowledge.upload(kb, "bomb.docx", inflated_docx(make_docx, 150_000))
    assert resp.status_code == 202, resp.text
    await knowledge.drain()
    doc = (await knowledge.client.get(f"/internal/v1/documents/{resp.json()['id']}")).json()
    assert doc["state"] == "FAILED" and doc["error"]["code"] == "DOCUMENT_TOO_COMPLEX"


async def test_a_slow_ingestion_is_abandoned_and_its_job_released(
    knowledge: Any, owner_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    kb = await knowledge.create_kb(owner_id)
    worker = knowledge.app.state.worker
    worker.settings = worker.settings.model_copy(update={"ingest_max_seconds": 0.5})

    def stuck(texts: list[str]) -> list[list[float]]:
        time.sleep(2)
        return HashingEmbedder().embed_documents(texts)

    worker.embedder.embed_documents = stuck
    resp = await knowledge.upload(kb, "a.txt", b"some text")
    started = time.monotonic()
    assert await worker.run_once()
    assert time.monotonic() - started < 1.9
    doc = (await knowledge.client.get(f"/internal/v1/documents/{resp.json()['id']}")).json()
    assert doc["state"] == "FAILED" and doc["error"]["code"] == "INGEST_TIMEOUT"
    async with sessions() as db:
        job = (await db.scalars(select(Job).where(Job.type == "knowledge.ingest"))).one()
        assert job.state == "dead"
    await asyncio.sleep(2)  # let the abandoned embedding thread finish


async def test_heartbeat_stops_at_the_deadline(knowledge: Any) -> None:
    worker = knowledge.app.state.worker
    await asyncio.wait_for(worker._heartbeat(1, 3, time.monotonic() + 0.5), 2)


async def test_documents_without_a_live_job_are_failed(
    knowledge: Any, owner_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    kb = await knowledge.create_kb(owner_id)
    orphan = (await knowledge.upload(kb, "a.txt", b"first")).json()["id"]
    queued = (await knowledge.upload(kb, "b.txt", b"second")).json()["id"]
    async with sessions() as db:
        # The process died mid-ingestion and the scheduler's reaper marked the job dead.
        await db.execute(
            update(Job).where(Job.payload["documentId"].as_string() == orphan).values(state="dead")
        )
        await db.execute(
            update(Document).where(Document.id == uuid.UUID(orphan)).values(state="PROCESSING")
        )
        await db.commit()
    worker = knowledge.app.state.worker
    assert await worker.sweep(grace_seconds=3600) == 0  # too recent to judge
    assert await worker.sweep(grace_seconds=0) == 1
    docs = {
        d["id"]: d
        for d in (await knowledge.client.get(f"/internal/v1/knowledge-bases/{kb}/documents")).json()
    }
    assert docs[orphan]["state"] == "FAILED"
    assert docs[orphan]["error"]["code"] == "INGEST_ABANDONED"
    assert docs[queued]["state"] == "PENDING"  # its job is still queued
    await knowledge.drain()
    retried = await knowledge.client.post(f"/internal/v1/documents/{orphan}/reindex")
    assert retried.status_code == 202
    await knowledge.drain()
    assert (await knowledge.client.get(f"/internal/v1/documents/{orphan}")).json()["state"] == (
        "READY"
    )


class MissingModel(HashingEmbedder):
    """Like the local embedder before ``cq-knowledge fetch-model`` has run."""

    def __init__(self) -> None:
        self.installed = False
        self.loads = 0
        self._ready = False

    @property  # type: ignore[override]
    def ready(self) -> bool:
        return self._ready

    def load(self) -> None:
        self.loads += 1
        if not self.installed:
            raise ModelUnavailable("The embedding model is not installed. Run fetch-model.")
        self._ready = True


@pytest.fixture
async def missing_model(
    settings: Settings,
    tmp_path: Path,
    sessions: async_sessionmaker[AsyncSession],
    person3_knowledge_tables: None,
) -> AsyncIterator[tuple[Any, MissingModel, httpx.AsyncClient]]:
    ks = KnowledgeSettings(**{**settings.model_dump(), "documents_dir": tmp_path / "documents"})
    embedder = MissingModel()
    app = create_app(ks, embedder=embedder, run_worker=False)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://knowledge",
        headers={"authorization": f"Bearer {ks.internal_service_token.get_secret_value()}"},
    )
    async with app.router.lifespan_context(app):
        yield app, embedder, client
    await client.aclose()


async def test_a_missing_model_fails_readiness_and_holds_ingestion(
    missing_model: tuple[Any, MissingModel, httpx.AsyncClient], owner_id: uuid.UUID
) -> None:
    app, embedder, client = missing_model
    assert embedder.loads == 1  # loaded at startup, not on the first upload
    ready = await client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["code"] == "EMBEDDING_MODEL_UNAVAILABLE"
    assert "fetch-model" in ready.json()["message"]
    assert (await client.get("/health/live")).status_code == 200

    kb = await client.post(
        "/internal/v1/knowledge-bases", json={"ownerId": str(owner_id), "name": "Docs"}
    )
    upload = await client.post(
        f"/internal/v1/knowledge-bases/{kb.json()['id']}/documents",
        files={"file": ("a.txt", b"Refunds are prorated.")},
    )
    worker = app.state.worker
    assert not await worker.run_once()  # the ingest job stays queued, not failed
    doc_url = f"/internal/v1/documents/{upload.json()['id']}"
    assert (await client.get(doc_url)).json()["state"] == "PENDING"

    embedder.installed = True  # the init container finished
    assert await worker.load_model()
    assert (await client.get("/health/ready")).json()["status"] == "ok"
    assert await worker.run_once()
    assert (await client.get(doc_url)).json()["state"] == "READY"
