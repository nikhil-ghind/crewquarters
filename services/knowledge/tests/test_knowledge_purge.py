"""Deleted document bytes: overwritten, removed only by the purge job, retried on failure."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from crewquarters_knowledge import service
from crewquarters_knowledge.config import KnowledgeSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_shared import jobs
from crewquarters_shared.db.models import Job


@pytest.mark.no_db
def test_overwrite_zeroes_the_bytes(tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_bytes(b"secret contract terms" * 100_000)
    service._overwrite(path)
    assert path.read_bytes() == bytes(len(b"secret contract terms") * 100_000)


@pytest.mark.no_db
def test_purge_removes_files_and_directories(tmp_path: Path) -> None:
    settings = KnowledgeSettings(documents_dir=tmp_path)
    (tmp_path / "kb" / "sub").mkdir(parents=True)
    (tmp_path / "kb" / "a.txt").write_text("a")
    (tmp_path / "kb" / "sub" / "b.txt").write_text("b")
    assert service.purge(settings, "kb/a.txt") == 1
    assert service.purge(settings, "kb") == 1
    assert not (tmp_path / "kb").exists()
    assert service.purge(settings, "kb") == 0  # already gone is success


@pytest.mark.no_db
@pytest.mark.parametrize("relative", ["../outside.txt", "/etc/hosts", ".", ""])
def test_purge_never_leaves_the_documents_directory(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("keep me")
    with pytest.raises(ValueError, match="outside"):
        service.purge(KnowledgeSettings(documents_dir=root), relative)
    assert (tmp_path / "outside.txt").read_text() == "keep me"


async def test_forged_purge_job_is_refused(
    knowledge: Any, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    outside = knowledge.settings.documents_dir.parent / "outside.txt"
    outside.write_text("keep me")
    async with sessions() as db:
        await jobs.enqueue(db, service.PURGE_JOB, {"path": "../outside.txt"})
        await db.commit()
    await knowledge.drain()
    assert outside.read_text() == "keep me"
    async with sessions() as db:
        job = (await db.scalars(select(Job).where(Job.type == service.PURGE_JOB))).one()
        assert job.state == "succeeded"


async def test_failed_purge_is_retried(
    knowledge: Any,
    owner_id: uuid.UUID,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kb = await knowledge.create_kb(owner_id)
    resp = await knowledge.upload(kb, "a.txt", b"bytes to purge")
    await knowledge.drain()

    def denied(settings: KnowledgeSettings, relative: str) -> int:
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(service, "purge", denied)
    await knowledge.client.delete(f"/internal/v1/documents/{resp.json()['id']}")
    await knowledge.drain()
    async with sessions() as db:
        job = (await db.scalars(select(Job).where(Job.type == service.PURGE_JOB))).one()
        assert (job.state, job.attempts) == ("available", 1)
    assert any(p.is_file() for p in (knowledge.settings.documents_dir / kb).iterdir())
    metrics = (await knowledge.client.get("/internal/v1/metrics")).text
    assert 'cq_knowledge_purges_total{outcome="retry"} 1.0' in metrics
