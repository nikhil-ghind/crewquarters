"""Restore a backup archive (``cq-admin backup restore``; the appliance runs it through
``crewquarters backup restore``). Restore is CLI-only: it replaces the whole database.

Steps: refuse while other sessions use the database (a running stack) unless forced,
verify every checksum, check that the backup's migration head is one this version knows
(same or older), restore the database and the documents, ``alembic upgrade head``, then
check every stored secret against the device master key. Secrets the current key cannot
decrypt (the backup came from another device and did not carry its key) are not an
opaque failure: their Google connections become ``NEEDS_ATTENTION`` and their Twilio,
OpenAI and Anthropic profiles ``ERROR``, so the UI asks the owner to reconnect them.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from crewquarters_secret_store import Keyring, SecretStoreError
from crewquarters_shared import audit
from crewquarters_shared.backup import (
    BackupError,
    Manifest,
    PgTarget,
    PgTools,
    other_connections,
    restore_database,
    restore_documents,
    verify_archive,
)
from crewquarters_shared.db import create_engine, session_factory

RECONNECT_DETAIL = (
    "Restored from a backup that was encrypted with a different device master key. "
    "Reconnect this account in Connections."
)


def alembic_ini() -> Path:
    """The control API's ``alembic.ini``: ``CQ_ALEMBIC_INI``, the image (``/app``), or the
    source tree."""
    candidates = [
        os.environ.get("CQ_ALEMBIC_INI"),
        Path.cwd() / "services/control_api/alembic.ini",
        Path(__file__).resolve().parents[2] / "alembic.ini",
        "/app/services/control_api/alembic.ini",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise BackupError("MIGRATIONS_NOT_FOUND", "Cannot find the control API's alembic.ini.")


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(alembic_ini()))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def check_compatible(manifest: Manifest, cfg: Config) -> str:
    """Return this version's head. Refuse a backup made by a newer version: its migration
    head is not in our history, and database downgrades are not assumed (PLAN.md 14.2)."""
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    assert head is not None
    known = {rev.revision for rev in script.walk_revisions()}
    if manifest.migration_head is not None and manifest.migration_head not in known:
        raise BackupError(
            "BACKUP_TOO_NEW",
            f"The backup's database schema ({manifest.migration_head}) is newer than this "
            f"version supports (head {head}). Install the Crewquarters version that made it "
            f"({manifest.platform_version}) or newer, then restore.",
        )
    return head


def _secret_context(row: Any) -> dict[str, str]:
    # Must match crewquarters_secret_store.db._context (tested in test_backup_restore).
    return {
        "id": str(row.id),
        "provider": row.provider,
        "owner": f"{row.owner_type}:{row.owner_id}",
    }


@dataclass
class SecretCheck:
    checked: int = 0
    unreadable: int = 0
    key_available: bool = True
    needs_attention: list[str] = field(default_factory=list)


async def check_secrets(database_url: str, keyring: Keyring | None) -> SecretCheck:
    """Mark connections whose secrets the device key cannot decrypt (see module doc)."""
    engine = create_engine(database_url, 2)
    result = SecretCheck(key_available=keyring is not None)
    try:
        async with session_factory(engine)() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, provider, owner_type, owner_id, ciphertext "
                        "FROM encrypted_secrets"
                    )
                )
            ).all()
            bad: list[Any] = []
            for row in rows:
                result.checked += 1
                try:
                    if keyring is None:
                        raise SecretStoreError("no key")
                    keyring.decrypt(bytes(row.ciphertext), _secret_context(row))
                except SecretStoreError:
                    bad.append(row.id)
            result.unreadable = len(bad)
            if bad:
                google = await db.execute(
                    text(
                        "UPDATE oauth_connections SET status = 'NEEDS_ATTENTION', "
                        "status_detail = :detail, updated_at = now() "
                        "WHERE encrypted_secret_id = ANY(:ids) RETURNING provider"
                    ),
                    {"ids": bad, "detail": RECONNECT_DETAIL},
                )
                profiles = await db.execute(
                    text(
                        "UPDATE provider_profiles SET status = 'ERROR', updated_at = now() "
                        "WHERE encrypted_secret_id = ANY(:ids) RETURNING provider"
                    ),
                    {"ids": bad},
                )
                result.needs_attention = sorted(
                    {r[0] for r in google.all()} | {r[0] for r in profiles.all()}
                )
            await db.commit()
    finally:
        await engine.dispose()
    return result


@dataclass
class RestoreReport:
    manifest: Manifest
    previous_head: str | None
    head: str
    documents_restored: int
    master_key_restored: bool
    secrets: SecretCheck
    installed_models: list[str]

    def lines(self) -> list[str]:
        out = [
            f"Restored backup from {self.manifest.created_at} "
            f"(version {self.manifest.platform_version}).",
            f"Database schema: {self.manifest.migration_head} -> {self.head}.",
            f"Documents restored: {self.documents_restored}.",
        ]
        if self.master_key_restored:
            out.append("The device master key was restored from the backup.")
        if not self.secrets.key_available and self.secrets.checked:
            out.append(
                "No device master key was available to check stored connections; all "
                f"{self.secrets.checked} stored secrets are marked for reconnection."
            )
        elif self.secrets.unreadable:
            out.append(
                f"{self.secrets.unreadable} of {self.secrets.checked} stored secrets cannot be "
                "decrypted with this device's master key (the backup did not include the key "
                "that encrypted them)."
            )
        if self.secrets.needs_attention:
            out.append("Reconnect in Connections: " + ", ".join(self.secrets.needs_attention) + ".")
        if self.installed_models:
            out.append(
                "Models recorded as installed: " + ", ".join(self.installed_models) + ". Model "
                "files are not in backups; if a model's files are missing on this device, "
                "download it again from Models."
            )
        return out


async def _installed_models(database_url: str) -> list[str]:
    from crewquarters_shared.db.models_gateway import ModelInstallation

    engine = create_engine(database_url, 2)
    try:
        async with session_factory(engine)() as db:
            rows = await db.scalars(
                select(ModelInstallation.model_id).where(ModelInstallation.state == "INSTALLED")
            )
            return sorted(rows.all())
    except Exception:
        return []
    finally:
        await engine.dispose()


async def _audit_restore(database_url: str, report: RestoreReport) -> None:
    engine = create_engine(database_url, 2)
    try:
        async with session_factory(engine)() as db:
            audit.record(
                db,
                action="system.backup_restored",
                actor_type="system",
                metadata={
                    "backupCreatedAt": report.manifest.created_at,
                    "platformVersion": report.manifest.platform_version,
                    "fromHead": report.manifest.migration_head,
                    "toHead": report.head,
                    "documents": report.documents_restored,
                    "masterKeyRestored": report.master_key_restored,
                    "unreadableSecrets": report.secrets.unreadable,
                },
            )
            await db.commit()
    finally:
        await engine.dispose()


def _load_keyring(path: Path | None, say: Callable[[str], None]) -> Keyring | None:
    if path is None or not path.exists():
        return None
    try:
        return Keyring.from_file(path)
    except (OSError, SecretStoreError) as exc:
        say(f"warning: cannot read the device master key ({exc}); secrets are not checked")
        return None


async def restore(
    archive: Path,
    database_url: str,
    documents_dir: Path,
    *,
    tools: PgTools | None = None,
    master_key_file: Path | None = None,
    restore_master_key: bool = False,
    force: bool = False,
    work_root: Path | None = None,
    say: Callable[[str], None] = print,
) -> RestoreReport:
    target = PgTarget.from_url(database_url)
    if not force:
        busy = other_connections(target)
        if busy:
            raise BackupError(
                "STACK_RUNNING",
                f"{busy} other database session(s) are open: the platform is running. Stop it "
                "first (crewquarters backup restore --stop), or pass --force.",
            )
    work = Path(tempfile.mkdtemp(prefix="cq-restore-", dir=work_root))
    try:
        say("Verifying checksums...")
        verified = verify_archive(archive, work)
        manifest = verified.manifest
        cfg = alembic_config(database_url)
        head = check_compatible(manifest, cfg)
        if restore_master_key:
            if verified.master_key is None:
                raise BackupError(
                    "BACKUP_HAS_NO_MASTER_KEY", "This backup does not include the master key."
                )
            if master_key_file is None:
                raise BackupError(
                    "MASTER_KEY_PATH_REQUIRED", "Pass --master-key-file to restore the key into."
                )
        say("Restoring the database...")
        restore_database(target, verified.database, tools)
        say("Restoring documents...")
        restored = restore_documents(verified.documents, documents_dir)
        say("Applying migrations...")
        command.upgrade(cfg, "head")
        key_restored = False
        if restore_master_key and verified.master_key is not None and master_key_file is not None:
            # Rewrite in place: keeps the file's owner and mode (root:crewquarters 0640).
            with master_key_file.open("wb") as out, verified.master_key.open("rb") as src:
                shutil.copyfileobj(src, out)
            key_restored = True
        say("Checking stored connections against the device master key...")
        secrets = await check_secrets(database_url, _load_keyring(master_key_file, say))
        report = RestoreReport(
            manifest=manifest,
            previous_head=manifest.migration_head,
            head=head,
            documents_restored=restored,
            master_key_restored=key_restored,
            secrets=secrets,
            installed_models=await _installed_models(database_url),
        )
        await _audit_restore(database_url, report)
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)
