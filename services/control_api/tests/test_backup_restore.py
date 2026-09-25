"""Backup, restore, and the owner backup API (PLAN.md sections 6, 10.2, 15.2, 23.2).

pg_dump/pg_restore must match the server's major version. The host usually has no
PostgreSQL client, so the tests run the tools inside the test database's container
(``CQ_TEST_PG_CONTAINER``, default ``crewquarters-postgres-1``) through ``docker exec``;
set ``CQ_TEST_PG_LOCAL=1`` to use a local pg_dump/pg_restore instead.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select, text

from conftest import ORIGIN, alembic_config, fresh_database, install_hello
from crewquarters_api import backups
from crewquarters_api.restore import (
    RECONNECT_DETAIL,
    check_compatible,
    check_secrets,
    restore,
)
from crewquarters_api.restore import (
    alembic_config as restore_alembic_config,
)
from crewquarters_secret_store import Keyring
from crewquarters_secret_store import db as secret_db
from crewquarters_shared import backup
from crewquarters_shared.config import Settings
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.db.models import AuditEvent, Job

CONTAINER = os.environ.get("CQ_TEST_PG_CONTAINER", "crewquarters-postgres-1")
ENV_ARGS = ["-e", "PGDATABASE", "-e", "PGUSER", "-e", "PGPASSWORD"]


def _pg_tools() -> backup.PgTools | None:
    if os.environ.get("CQ_TEST_PG_LOCAL") == "1" and shutil.which("pg_dump"):
        return backup.PgTools()
    if shutil.which("docker") is None:
        return None
    probe = subprocess.run(
        ["docker", "exec", CONTAINER, "pg_dump", "--version"],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    base = ("docker", "exec", "-i", *ENV_ARGS, CONTAINER)
    return backup.PgTools(dump=(*base, "pg_dump"), restore=(*base, "pg_restore"))


TOOLS = _pg_tools()
needs_pg_tools = pytest.mark.skipif(
    TOOLS is None, reason="no pg_dump/pg_restore (docker exec into the test database)"
)


def tools_string(parts: tuple[str, ...]) -> str:
    return " ".join(parts)


@pytest.fixture
def scratch_db() -> Iterator[str]:
    """A separate, migrated database the test may destroy and restore."""
    with fresh_database() as url:
        command.upgrade(alembic_config(url), "head")
        yield url


def _sync(url: str) -> Any:
    return create_sync_engine(url)


def _seed(url: str, documents: Path) -> dict[str, Any]:
    """An owner, a knowledge base with one document row and its file, and a setting."""
    engine = _sync(url)
    user_id, kb_id, doc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, username, username_normalized, password_hash, role, "
                "created_at) VALUES (:id, 'owner', 'owner', 'x', 'owner', now())"
            ),
            {"id": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO knowledge_bases (id, owner_id, name, embedding_profile, "
                "embedding_dimension, created_at) VALUES (:id, :owner, 'Notes', 'fake', 512, "
                "now())"
            ),
            {"id": kb_id, "owner": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, kb_id, name, mime, path, sha256, bytes, state, "
                "extracted, created_at, updated_at) VALUES (:id, :kb, 'notes.txt', "
                "'text/plain', :path, 'abc', 5, 'READY', '{}', now(), now())"
            ),
            {"id": doc_id, "kb": kb_id, "path": f"{kb_id}/{doc_id}.txt"},
        )
        conn.execute(
            text(
                "INSERT INTO settings (key, value, value_type, version, updated_at) "
                "VALUES ('timezone', '\"Asia/Kolkata\"', 'string', 1, now())"
            )
        )
    engine.dispose()
    (documents / str(kb_id)).mkdir(parents=True)
    (documents / str(kb_id) / f"{doc_id}.txt").write_text("hello")
    (documents / "orphan.bin").write_text("not referenced")  # not in the database
    return {"user": user_id, "kb": kb_id, "doc": doc_id}


def _count(url: str, table: str) -> int:
    engine = _sync(url)
    with engine.connect() as conn:
        value = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
    engine.dispose()
    return int(value)


# --- Archive format -------------------------------------------------------------------------


@needs_pg_tools
def test_create_backup_archive_contents_and_permissions(scratch_db: str, tmp_path: Path) -> None:
    assert TOOLS is not None
    documents = tmp_path / "documents"
    ids = _seed(scratch_db, documents)
    result = backup.create_backup(
        tmp_path / "out", scratch_db, documents, tools=TOOLS, platform_version="9.9.9", label="t"
    )
    assert result.path.name.endswith("-t.tar.gz")
    assert result.path.stat().st_mode & 0o777 == 0o600
    sidecar = backup.sidecar_path(result.path.parent, result.name)
    assert sidecar.stat().st_mode & 0o777 == 0o600
    manifest = result.manifest.to_json()
    assert manifest["includesMasterKey"] is False
    assert manifest["migrationHead"] == "0004"
    assert manifest["platformVersion"] == "9.9.9"
    assert manifest["documents"] == {"count": 1, "missing": 0, "bytes": 5}
    assert set(manifest["parts"]) == {"database.dump", "documents.tar"}
    with tarfile.open(result.path) as tar:
        names = tar.getnames()
        assert names[0] == "manifest.json"
        assert "master.key" not in names
        inner = tar.extractfile("documents.tar").read()  # type: ignore[union-attr]
    with tarfile.open(fileobj=io.BytesIO(inner)) as docs:
        # Only files the dumped rows reference: the orphan is not included.
        assert docs.getnames() == [f"{ids['kb']}/{ids['doc']}.txt"]
    assert json.loads(sidecar.read_text())["archive"]["sha256"] == result.sha256
    # No scratch left behind.
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(
        [result.path.name, sidecar.name]
    )


@needs_pg_tools
def test_master_key_is_included_only_on_request(scratch_db: str, tmp_path: Path) -> None:
    assert TOOLS is not None
    key = tmp_path / "master.key"
    key.write_text(f"1:{'ab' * 32}\n")
    key.chmod(0o600)
    result = backup.create_backup(
        tmp_path / "out",
        scratch_db,
        tmp_path / "docs",
        tools=TOOLS,
        platform_version="1",
        master_key_file=key,
    )
    assert result.manifest.includes_master_key
    with tarfile.open(result.path) as tar:
        assert "master.key" in tar.getnames()
    work = tmp_path / "verify"
    work.mkdir()
    verified = backup.verify_archive(result.path, work)
    assert verified.master_key is not None
    assert verified.master_key.read_text() == key.read_text()


@needs_pg_tools
def test_verify_detects_tampering(scratch_db: str, tmp_path: Path) -> None:
    assert TOOLS is not None
    result = backup.create_backup(
        tmp_path / "out", scratch_db, tmp_path / "docs", tools=TOOLS, platform_version="1"
    )
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(result.path) as src, tarfile.open(tampered, "w:gz") as dst:
        for member in src:
            data = src.extractfile(member).read()  # type: ignore[union-attr]
            if member.name == "database.dump":
                data = data[:-1] + bytes([data[-1] ^ 1])
            dst.addfile(member, io.BytesIO(data))
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(backup.BackupError) as info:
        backup.verify_archive(tampered, work)
    assert info.value.code == "CHECKSUM_MISMATCH"


def test_verify_rejects_unexpected_entries(tmp_path: Path) -> None:
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("../../etc/passwd")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(backup.BackupError) as info_:
        backup.verify_archive(evil, work)
    assert info_.value.code == "INVALID_ARCHIVE"
    not_a_backup = tmp_path / "x.tar.gz"
    not_a_backup.write_text("nope")
    with pytest.raises(backup.BackupError):
        backup.verify_archive(not_a_backup, work)


def test_retention_keeps_newest_and_ignores_other_files(tmp_path: Path) -> None:
    for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        name = f"crewquarters-backup-{stamp}"
        (tmp_path / f"{name}.tar.gz").write_text("x")
        (tmp_path / f"{name}.manifest.json").write_text("{}")
    (tmp_path / "pre-upgrade-0.1.0.sql.gz").write_text("keep me")
    removed = backup.prune(tmp_path, 2)
    assert removed == ["crewquarters-backup-20260101T000000Z"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "crewquarters-backup-20260102T000000Z.manifest.json",
        "crewquarters-backup-20260102T000000Z.tar.gz",
        "crewquarters-backup-20260103T000000Z.manifest.json",
        "crewquarters-backup-20260103T000000Z.tar.gz",
        "pre-upgrade-0.1.0.sql.gz",
    ]


def test_backup_names_are_validated(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError):
        backup.archive_path(tmp_path, "../../etc/passwd")
    with pytest.raises(backup.BackupError):
        backup.backup_name(backup.datetime.now(backup.UTC), "Bad Label")


# --- Restore ---------------------------------------------------------------------------------


@needs_pg_tools
async def test_restore_round_trip_older_head_and_documents(tmp_path: Path) -> None:
    """A backup made at an older migration head restores and is upgraded to head; the
    documents it references come back; local changes after the backup are gone."""
    assert TOOLS is not None
    with fresh_database() as url:
        command.upgrade(alembic_config(url), "0003")
        documents = tmp_path / "documents"
        ids = _seed(url, documents)
        result = backup.create_backup(
            tmp_path / "out", url, documents, tools=TOOLS, platform_version="0.0.9"
        )
        assert result.manifest.migration_head == "0003"
        # Diverge after the backup: a new setting, a deleted document file, a stray file.
        engine = _sync(url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM settings"))
        engine.dispose()
        shutil.rmtree(documents / str(ids["kb"]))
        (documents / "later.txt").write_text("created after the backup")
        (documents / ".staging").mkdir()
        (documents / ".staging" / "upload.part").write_text("in flight")

        report = await restore(
            result.path, url, documents, tools=TOOLS, master_key_file=None, say=lambda _: None
        )
        assert report.head == "0004" and report.previous_head == "0003"
        assert report.documents_restored == 1
        assert _count(url, "settings") == 1
        assert _count(url, "documents") == 1
        engine = _sync(url)
        with engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            restored_audit = conn.execute(
                text("SELECT count(*) FROM audit_events WHERE action = 'system.backup_restored'")
            ).scalar_one()
        engine.dispose()
        assert head == "0004"
        assert restored_audit == 1
        assert (documents / str(ids["kb"]) / f"{ids['doc']}.txt").read_text() == "hello"
        assert not (documents / "later.txt").exists()
        assert (documents / ".staging" / "upload.part").exists()  # in-flight uploads kept


@needs_pg_tools
async def test_restore_refuses_while_other_sessions_are_connected(
    scratch_db: str, tmp_path: Path
) -> None:
    assert TOOLS is not None
    result = backup.create_backup(
        tmp_path / "out", scratch_db, tmp_path / "docs", tools=TOOLS, platform_version="1"
    )
    engine = _sync(scratch_db)
    with engine.connect() as held:  # a "running stack"
        held.execute(text("SELECT 1"))
        with pytest.raises(backup.BackupError) as info:
            await restore(result.path, scratch_db, tmp_path / "docs", tools=TOOLS, say=print)
    engine.dispose()
    assert info.value.code == "STACK_RUNNING"


def test_restore_refuses_a_backup_from_a_newer_version(tmp_path: Path) -> None:
    manifest = backup.Manifest(
        created_at="2027-01-01T00:00:00Z",
        platform_version="9.0.0",
        migration_head="9999_from_the_future",
        postgres_version="16",
        parts={},
        documents={},
        includes_master_key=False,
    )
    with pytest.raises(backup.BackupError) as info:
        check_compatible(manifest, restore_alembic_config("postgresql+psycopg://x@h/db"))
    assert info.value.code == "BACKUP_TOO_NEW"
    manifest.migration_head = "0002"
    assert check_compatible(manifest, restore_alembic_config("postgresql+psycopg://x@h/db"))


async def _seed_connections(url: str, keyring: Keyring) -> dict[str, uuid.UUID]:
    from crewquarters_broker.models import OAuthConnection

    engine = create_engine(url, 2)
    try:
        async with session_factory(engine)() as db:
            user_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO users (id, username, username_normalized, password_hash, "
                    "role, created_at) VALUES (:id, 'o', 'o', 'x', 'owner', now())"
                ),
                {"id": user_id},
            )
            google_id, twilio_id = uuid.uuid4(), uuid.uuid4()
            google_secret = await secret_db.store(
                db,
                keyring,
                provider="google",
                owner_type="oauth_connection",
                owner_id=google_id,
                plaintext=b"refresh-token",
            )
            db.add(
                OAuthConnection(
                    id=google_id,
                    user_id=user_id,
                    provider="google",
                    provider_subject="demo@example.com",
                    scopes=[],
                    encrypted_secret_id=google_secret.id,
                    status="CONNECTED",
                )
            )
            twilio_secret = await secret_db.store(
                db,
                keyring,
                provider="twilio",
                owner_type="provider_profile",
                owner_id=twilio_id,
                plaintext=b"auth-token",
            )
            db.add(
                secret_db.ProviderProfile(
                    id=twilio_id,
                    owner_id=user_id,
                    provider="twilio",
                    display_name="Twilio",
                    encrypted_secret_id=twilio_secret.id,
                    status="CONNECTED",
                )
            )
            await db.commit()
            return {"google": google_id, "twilio": twilio_id}
    finally:
        await engine.dispose()


def _statuses(url: str) -> tuple[str, str | None, str]:
    engine = _sync(url)
    with engine.connect() as conn:
        google = conn.execute(text("SELECT status, status_detail FROM oauth_connections")).one()
        twilio = conn.execute(text("SELECT status FROM provider_profiles")).scalar_one()
    engine.dispose()
    return google[0], google[1], twilio


async def test_secrets_the_device_key_cannot_decrypt_need_attention(scratch_db: str) -> None:
    """The backup's key differs from the device key: connections ask to be reconnected
    instead of failing opaquely. The same key leaves them connected."""
    original = Keyring({1: os.urandom(32)})
    await _seed_connections(scratch_db, original)

    same = await check_secrets(scratch_db, original)
    assert (same.checked, same.unreadable, same.needs_attention) == (2, 0, [])
    assert _statuses(scratch_db) == ("CONNECTED", None, "CONNECTED")

    other = await check_secrets(scratch_db, Keyring({1: os.urandom(32)}))
    assert (other.checked, other.unreadable) == (2, 2)
    assert other.needs_attention == ["google", "twilio"]
    assert _statuses(scratch_db) == ("NEEDS_ATTENTION", RECONNECT_DETAIL, "ERROR")


async def test_no_key_at_all_marks_every_secret(scratch_db: str) -> None:
    await _seed_connections(scratch_db, Keyring({1: os.urandom(32)}))
    result = await check_secrets(scratch_db, None)
    assert not result.key_available and result.unreadable == 2


@needs_pg_tools
async def test_restore_with_the_master_key_keeps_connections(tmp_path: Path) -> None:
    """Backup with --include-master-key on device A, restore on device B with
    --restore-master-key: the key file is rewritten and connections stay connected."""
    assert TOOLS is not None
    key_a = tmp_path / "a.key"
    key_a.write_text(f"1:{os.urandom(32).hex()}\n")
    key_a.chmod(0o600)
    key_b = tmp_path / "b.key"
    key_b.write_text(f"1:{os.urandom(32).hex()}\n")
    key_b.chmod(0o640)
    with fresh_database() as url:
        command.upgrade(alembic_config(url), "head")
        await _seed_connections(url, Keyring.from_file(key_a))
        result = backup.create_backup(
            tmp_path / "out",
            url,
            tmp_path / "docs",
            tools=TOOLS,
            platform_version="1",
            master_key_file=key_a,
        )
        # Device B without the key: connections need attention.
        report = await restore(
            result.path, url, tmp_path / "docs", tools=TOOLS, master_key_file=key_b, say=print
        )
        assert report.secrets.needs_attention == ["google", "twilio"]
        assert any("Reconnect in Connections" in line for line in report.lines())
        # Again, restoring the key this time.
        report = await restore(
            result.path,
            url,
            tmp_path / "docs",
            tools=TOOLS,
            master_key_file=key_b,
            restore_master_key=True,
            say=print,
        )
        assert report.master_key_restored
        assert key_b.read_text() == key_a.read_text()
        assert key_b.stat().st_mode & 0o777 == 0o640  # rewritten in place
        assert report.secrets.unreadable == 0
        assert _statuses(url)[0] == "CONNECTED"


# --- Owner API -------------------------------------------------------------------------------


@pytest.fixture
async def backup_client(
    settings: Settings, tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, Settings]]:
    from crewquarters_api.main import create_app

    assert TOOLS is not None
    configured = settings.model_copy(
        update={
            "backup_dir": tmp_path / "backups",
            "documents_dir": tmp_path / "documents",
            "backup_retention": 2,
            "pg_dump": tools_string(TOOLS.dump),
            "pg_restore": tools_string(TOOLS.restore),
        }
    )
    app = create_app(configured, run_backup_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as client:
        yield client, configured
    await app.state.engine.dispose()


async def _sign_in(client: httpx.AsyncClient, sessions: Any) -> None:
    from conftest import PASSWORD, issue_bootstrap_token

    token = await issue_bootstrap_token(sessions)
    response = await client.post(
        "/api/v1/bootstrap", json={"token": token, "username": "owner", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrfToken"]


@needs_pg_tools
@pytest.mark.usefixtures("catalog_synced")
async def test_backup_api_create_list_download(
    backup_client: tuple[httpx.AsyncClient, Settings], sessions: Any
) -> None:
    client, configured = backup_client
    assert (await client.get("/api/v1/system/backups")).status_code == 401
    await _sign_in(client, sessions)
    await install_hello(client)

    empty = (await client.get("/api/v1/system/backups")).json()
    assert empty["items"] == [] and empty["enabled"] is True and empty["retention"] == 2

    # CSRF is required.
    no_csrf = await client.post("/api/v1/system/backups", headers={"X-CSRF-Token": "wrong"})
    assert no_csrf.status_code == 403

    created = await client.post(
        "/api/v1/system/backups", headers={"Idempotency-Key": "backup-key-1234"}
    )
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "queued" and body["source"] == "api"
    assert body["includesMasterKey"] is False
    replay = await client.post(
        "/api/v1/system/backups", headers={"Idempotency-Key": "backup-key-1234"}
    )
    assert replay.json()["id"] == body["id"]
    busy = await client.post("/api/v1/system/backups")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "BACKUP_IN_PROGRESS"
    assert busy.json()["error"]["details"]["backupId"] == body["id"]

    worker = backups.BackupWorker(sessions, configured, "test-backup-worker")
    assert await worker.run_once()
    assert not await worker.run_once()

    listed = (await client.get("/api/v1/system/backups")).json()["items"]
    assert [i["id"] for i in listed] == [body["id"]]
    item = listed[0]
    assert item["status"] == "succeeded" and item["downloadable"] is True
    assert item["sizeBytes"] > 0 and item["migrationHead"] and item["sha256"]

    download = await client.get(f"/api/v1/system/backups/{body['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/gzip"
    assert "attachment" in download.headers["content-disposition"]
    with tarfile.open(fileobj=io.BytesIO(download.content)) as tar:
        assert "master.key" not in tar.getnames()
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
    assert manifest["includesMasterKey"] is False
    import hashlib

    assert hashlib.sha256(download.content).hexdigest() == item["sha256"]

    async with sessions() as db:
        actions = (
            await db.scalars(
                select(AuditEvent.action).where(AuditEvent.action.startswith("system.backup"))
            )
        ).all()
    assert set(actions) >= {
        "system.backup_requested",
        "system.backup_created",
        "system.backup_downloaded",
    }

    missing = await client.get(
        "/api/v1/system/backups/crewquarters-backup-20200101T000000Z/download"
    )
    assert missing.status_code == 404
    bad = await client.get("/api/v1/system/backups/..%2F..%2Fetc%2Fpasswd/download")
    assert bad.status_code == 404


@needs_pg_tools
async def test_backups_with_the_master_key_are_listed_but_not_downloadable(
    backup_client: tuple[httpx.AsyncClient, Settings], sessions: Any, tmp_path: Path
) -> None:
    client, configured = backup_client
    await _sign_in(client, sessions)
    key = tmp_path / "master.key"
    key.write_text(f"1:{'cd' * 32}\n")
    key.chmod(0o600)
    assert TOOLS is not None and configured.backup_dir is not None
    made = backup.create_backup(
        configured.backup_dir,
        configured.database_url,
        configured.documents_dir,
        tools=TOOLS,
        platform_version="1",
        master_key_file=key,
    )
    listed = (await client.get("/api/v1/system/backups")).json()["items"]
    assert listed[0]["id"] == made.name
    assert listed[0]["source"] == "device" and listed[0]["includesMasterKey"] is True
    assert listed[0]["downloadable"] is False
    denied = await client.get(f"/api/v1/system/backups/{made.name}/download")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "BACKUP_CONTAINS_MASTER_KEY"


@needs_pg_tools
async def test_failed_backup_job_is_reported(
    backup_client: tuple[httpx.AsyncClient, Settings], sessions: Any
) -> None:
    client, configured = backup_client
    await _sign_in(client, sessions)
    created = (await client.post("/api/v1/system/backups")).json()
    broken = configured.model_copy(update={"pg_dump": "/nonexistent/pg_dump"})
    assert await backups.BackupWorker(sessions, broken, "w").run_once()
    item = (await client.get("/api/v1/system/backups")).json()["items"][0]
    assert item["id"] == created["id"] and item["status"] == "failed"
    assert item["error"]["code"] == "PG_DUMP_FAILED"
    async with sessions() as db:
        job = (await db.scalars(select(Job).where(Job.type == backups.JOB_BACKUP))).one()
    assert job.state == "dead"
    # A new backup can be requested after a failure.
    assert (await client.post("/api/v1/system/backups")).status_code == 202


async def test_backups_not_configured(owner: httpx.AsyncClient) -> None:
    listed = (await owner.get("/api/v1/system/backups")).json()
    assert listed["enabled"] is False and listed["items"] == []
    refused = await owner.post("/api/v1/system/backups")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "BACKUPS_NOT_CONFIGURED"


async def test_bootstrap_status_is_public(client: httpx.AsyncClient, sessions: Any) -> None:
    status = await client.get("/api/v1/bootstrap/status")
    assert status.status_code == 200 and status.json() == {"ownerExists": False}
    await _sign_in(client, sessions)
    client.cookies.clear()
    status = await client.get("/api/v1/bootstrap/status")
    assert status.json() == {"ownerExists": True}
