"""Backup archives: one consistent snapshot of the PostgreSQL database and the documents
it references (PLAN.md sections 6, 10.2, 15.2).

Archive layout (``crewquarters-backup-<UTC stamp>[-<label>].tar.gz``, mode 0600)::

    manifest.json    format, platform version, migration head, created_at, sha256 and
                     size of every other part, whether the master key is included
    database.dump    pg_dump custom format of the one database
    documents.tar    the document files the dumped ``documents`` rows point at
    master.key       only with an explicit flag (never through the API)

Consistency: the database snapshot is exported once (``pg_export_snapshot``). The list
of document files is read in that snapshot and ``pg_dump --snapshot`` dumps exactly that
snapshot, so the documents in the archive are the ones the dumped rows reference. A file
deleted after the snapshot (purges run only after the row is gone) is counted in
``documents.missing``.

A sidecar ``<name>.manifest.json`` (0600) next to each archive lets a listing read the
manifest without opening the archive.

Everything here is synchronous; async callers use ``asyncio.to_thread``.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

FORMAT_VERSION = 1
PREFIX = "crewquarters-backup-"
SUFFIX = ".tar.gz"
SIDECAR = ".manifest.json"
MANIFEST = "manifest.json"
PART_DATABASE = "database.dump"
PART_DOCUMENTS = "documents.tar"
PART_MASTER_KEY = "master.key"
PARTS = (PART_DATABASE, PART_DOCUMENTS, PART_MASTER_KEY)
NAME_RE = re.compile(r"^crewquarters-backup-\d{8}T\d{6}Z(?:-[a-z0-9][a-z0-9-]{0,39})?$")
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# Directory entries a restore never replaces: in-flight uploads and our own scratch space.
DOCUMENTS_SKIP = frozenset({".staging", ".restore-new", ".restore-old"})
MASTER_KEY_WARNING = (
    "WARNING: this backup contains the device master key. Anyone who has the file can "
    "decrypt every stored Google, Twilio, OpenAI and Anthropic credential. Store it offline, "
    "encrypted, and delete it when it is no longer needed."
)
_COPY_CHUNK = 1024 * 1024


class BackupError(Exception):
    """A backup or restore step failed. ``code`` is a stable machine-readable reason;
    messages never contain secrets."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- PostgreSQL connection and client tools ------------------------------------------------------


@dataclass(frozen=True)
class PgTarget:
    """Connection parameters, passed to pg_dump/pg_restore through libpq variables so the
    password never appears on a command line."""

    host: str | None
    port: int | None
    user: str | None
    password: str | None
    dbname: str

    @classmethod
    def from_url(cls, url: str) -> PgTarget:
        parsed = make_url(url)
        if not parsed.database:
            raise BackupError("INVALID_DATABASE_URL", "The database URL names no database.")
        return cls(
            host=parsed.host,
            port=parsed.port,
            user=parsed.username,
            password=parsed.password if isinstance(parsed.password, str) else None,
            dbname=parsed.database,
        )

    def env(self) -> dict[str, str]:
        env = {"PGDATABASE": self.dbname}
        if self.host:
            env["PGHOST"] = self.host
        if self.port:
            env["PGPORT"] = str(self.port)
        if self.user:
            env["PGUSER"] = self.user
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        return env

    def connect(self, *, autocommit: bool = False) -> psycopg.Connection[Any]:
        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.dbname,
            autocommit=autocommit,
            connect_timeout=10,
        )


@dataclass(frozen=True)
class PgTools:
    """Command lines for pg_dump and pg_restore (``CQ_PG_DUMP`` / ``CQ_PG_RESTORE``)."""

    dump: tuple[str, ...] = ("pg_dump",)
    restore: tuple[str, ...] = ("pg_restore",)

    @classmethod
    def from_strings(cls, dump: str, restore: str) -> PgTools:
        return cls(tuple(shlex.split(dump)), tuple(shlex.split(restore)))


def _run_pg(
    argv: list[str],
    target: PgTarget,
    *,
    stdin: Any = None,
    stdout: Any = None,
    code: str,
) -> None:
    env = {**os.environ, **target.env()}
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            argv, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, env=env, check=False
        )
    except FileNotFoundError:
        raise BackupError(code, f"{argv[0]} is not installed in this environment.") from None
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        tail = " ".join(detail[-3:])[:500]
        raise BackupError(code, f"{Path(argv[0]).name} failed ({result.returncode}): {tail}")


# --- Manifest ------------------------------------------------------------------------------------


@dataclass
class Manifest:
    created_at: str
    platform_version: str
    migration_head: str | None
    postgres_version: str
    parts: dict[str, dict[str, Any]]
    documents: dict[str, int]
    includes_master_key: bool
    format_version: int = FORMAT_VERSION
    label: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "formatVersion": self.format_version,
            "createdAt": self.created_at,
            "platformVersion": self.platform_version,
            "migrationHead": self.migration_head,
            "postgresVersion": self.postgres_version,
            "label": self.label,
            "includesMasterKey": self.includes_master_key,
            "documents": self.documents,
            "parts": self.parts,
            **self.extra,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Manifest:
        try:
            known = {
                "formatVersion",
                "createdAt",
                "platformVersion",
                "migrationHead",
                "postgresVersion",
                "label",
                "includesMasterKey",
                "documents",
                "parts",
            }
            return cls(
                format_version=int(data["formatVersion"]),
                created_at=str(data["createdAt"]),
                platform_version=str(data["platformVersion"]),
                migration_head=data.get("migrationHead"),
                postgres_version=str(data.get("postgresVersion", "")),
                label=data.get("label"),
                includes_master_key=bool(data["includesMasterKey"]),
                documents={k: int(v) for k, v in dict(data.get("documents") or {}).items()},
                parts={str(k): dict(v) for k, v in dict(data["parts"]).items()},
                extra={k: v for k, v in data.items() if k not in known},
            )
        except (KeyError, TypeError, ValueError):
            raise BackupError("INVALID_MANIFEST", "The backup manifest is malformed.") from None


@dataclass(frozen=True)
class BackupResult:
    name: str
    path: Path
    size_bytes: int
    sha256: str
    manifest: Manifest


# --- Helpers -------------------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_name(now: datetime, label: str | None = None) -> str:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    if label is not None and not LABEL_RE.match(label):
        raise BackupError("INVALID_LABEL", "A label is 1-40 lowercase letters, digits or hyphens.")
    return f"{PREFIX}{stamp}" + (f"-{label}" if label else "")


def archive_path(directory: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        raise BackupError("INVALID_BACKUP_ID", "Not a backup name.")
    return directory / f"{name}{SUFFIX}"


def sidecar_path(directory: Path, name: str) -> Path:
    return directory / f"{name}{SIDECAR}"


def _private_file(path: Path) -> io.BufferedWriter:
    """Create ``path`` exclusively with mode 0600, whatever the umask."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb")


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    with _private_file(tmp) as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True).encode())
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


# --- Create --------------------------------------------------------------------------------------


def _snapshot_facts(cur: psycopg.Cursor[Any]) -> tuple[str | None, list[str], str]:
    head = None
    if cur.execute("SELECT to_regclass('public.alembic_version')").fetchone() != (None,):
        row = cur.execute("SELECT version_num FROM alembic_version").fetchone()
        head = str(row[0]) if row else None
    paths: list[str] = []
    if cur.execute("SELECT to_regclass('public.documents')").fetchone() != (None,):
        paths = [str(r[0]) for r in cur.execute("SELECT path FROM documents ORDER BY path")]
    version = str(cur.execute("SHOW server_version").fetchone()[0])  # type: ignore[index]
    return head, paths, version


def _tar_documents(documents_dir: Path, paths: list[str], out: Path) -> dict[str, int]:
    included = missing = total = 0
    with _private_file(out) as raw, tarfile.open(fileobj=raw, mode="w") as tar:
        for relative in paths:
            source = documents_dir / relative
            if Path(relative).is_absolute() or not _inside(documents_dir, source):
                missing += 1  # never follow a path outside the document store
                continue
            if not source.is_file():
                missing += 1
                continue
            info = tar.gettarinfo(str(source), arcname=relative)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o640
            with source.open("rb") as handle:
                tar.addfile(info, handle)
            included += 1
            total += info.size
    return {"count": included, "missing": missing, "bytes": total}


def create_backup(
    out_dir: Path,
    database_url: str,
    documents_dir: Path,
    *,
    tools: PgTools | None = None,
    platform_version: str,
    master_key_file: Path | None = None,
    label: str | None = None,
    name: str | None = None,
    now: datetime | None = None,
) -> BackupResult:
    """Write one backup archive into ``out_dir`` and return its description.

    ``master_key_file`` adds the device master key; callers must warn loudly
    (:data:`MASTER_KEY_WARNING`). The API path never passes it.
    """
    tools = tools or PgTools()
    target = PgTarget.from_url(database_url)
    now = now or datetime.now(UTC)
    name = name or backup_name(now, label)
    final = archive_path(out_dir, name)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if final.exists():
        raise BackupError("BACKUP_EXISTS", f"{final.name} already exists.")
    work = Path(tempfile.mkdtemp(prefix=".tmp-", dir=out_dir))
    try:
        dump = work / PART_DATABASE
        documents = work / PART_DOCUMENTS
        try:
            conn = target.connect()
        except psycopg.Error as exc:
            raise BackupError(
                "DATABASE_UNAVAILABLE", f"Cannot connect to the database: {type(exc).__name__}"
            ) from None
        with conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            with conn.cursor() as cur:
                snapshot = str(cur.execute("SELECT pg_export_snapshot()").fetchone()[0])  # type: ignore[index]
                head, paths, pg_version = _snapshot_facts(cur)
            # The snapshot stays valid while this transaction is open.
            with _private_file(dump) as handle:
                _run_pg(
                    [*tools.dump, "--format=custom", f"--snapshot={snapshot}", "--no-password"],
                    target,
                    stdout=handle,
                    code="PG_DUMP_FAILED",
                )
            doc_stats = _tar_documents(documents_dir, paths, documents)
            conn.rollback()
        parts: dict[str, dict[str, Any]] = {}
        for part in (dump, documents):
            parts[part.name] = {"sha256": sha256_file(part), "bytes": part.stat().st_size}
        key_copy: Path | None = None
        if master_key_file is not None:
            key_copy = work / PART_MASTER_KEY
            with _private_file(key_copy) as handle:
                handle.write(master_key_file.read_bytes())
            parts[PART_MASTER_KEY] = {
                "sha256": sha256_file(key_copy),
                "bytes": key_copy.stat().st_size,
            }
        manifest = Manifest(
            created_at=now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            platform_version=platform_version,
            migration_head=head,
            postgres_version=pg_version,
            parts=parts,
            documents=doc_stats,
            includes_master_key=key_copy is not None,
            label=label,
        )
        partial = work / f"{name}{SUFFIX}"
        with _private_file(partial) as raw:
            with tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6) as tar:
                body = json.dumps(manifest.to_json(), indent=2, sort_keys=True).encode()
                info = tarfile.TarInfo(MANIFEST)
                info.size, info.mode, info.mtime = len(body), 0o600, int(now.timestamp())
                tar.addfile(info, io.BytesIO(body))
                for part in [p for p in (dump, documents, key_copy) if p is not None]:
                    info = tar.gettarinfo(str(part), arcname=part.name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o600
                    with part.open("rb") as handle:
                        tar.addfile(info, handle)
            raw.flush()
            os.fsync(raw.fileno())
        digest = sha256_file(partial)
        size = partial.stat().st_size
        _write_private_json(
            sidecar_path(out_dir, name),
            {**manifest.to_json(), "archive": {"sha256": digest, "bytes": size}},
        )
        partial.replace(final)
        return BackupResult(
            name=name, path=final, size_bytes=size, sha256=digest, manifest=manifest
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- List, retention -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupFile:
    name: str
    path: Path
    size_bytes: int
    modified_at: datetime
    manifest: dict[str, Any] | None


def list_backups(directory: Path) -> list[BackupFile]:
    """Archives in ``directory``, newest first. Unreadable sidecars give ``manifest=None``."""
    if not directory.is_dir():
        return []
    found: list[BackupFile] = []
    for entry in directory.iterdir():
        if not entry.name.endswith(SUFFIX):
            continue
        name = entry.name[: -len(SUFFIX)]
        if not NAME_RE.match(name) or not entry.is_file():
            continue
        manifest: dict[str, Any] | None
        try:
            manifest = json.loads(sidecar_path(directory, name).read_text())
        except (OSError, ValueError):
            manifest = None
        stat = entry.stat()
        found.append(
            BackupFile(
                name=name,
                path=entry,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                manifest=manifest,
            )
        )
    return sorted(found, key=lambda b: b.name, reverse=True)


def prune(directory: Path, keep: int) -> list[str]:
    """Delete all but the newest ``keep`` archives (and their sidecars). Returns the names
    removed. Only files matching the backup name pattern are touched."""
    removed: list[str] = []
    for old in list_backups(directory)[max(keep, 1) :]:
        old.path.unlink(missing_ok=True)
        sidecar_path(directory, old.name).unlink(missing_ok=True)
        removed.append(old.name)
    return removed


def clean_stale_work(directory: Path, older_than_seconds: float = 3600) -> None:
    """Remove scratch directories left by an interrupted backup."""
    if not directory.is_dir():
        return
    cutoff = datetime.now(UTC).timestamp() - older_than_seconds
    for entry in directory.glob(".tmp-*"):
        with contextlib.suppress(OSError):
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)


def read_manifest(directory: Path, name: str) -> dict[str, Any]:
    """The backup's manifest: from its sidecar, else from the archive itself."""
    with contextlib.suppress(OSError, ValueError):
        data: dict[str, Any] = json.loads(sidecar_path(directory, name).read_text())
        return data
    try:
        with tarfile.open(archive_path(directory, name), mode="r:gz") as tar:
            member = tar.getmember(MANIFEST)
            source = tar.extractfile(member)
            if source is None or member.size > 1024 * 1024:
                raise KeyError(MANIFEST)
            loaded: dict[str, Any] = json.loads(source.read())
            return loaded
    except (OSError, KeyError, ValueError, tarfile.TarError):
        raise BackupError("INVALID_ARCHIVE", "The backup's manifest cannot be read.") from None


# --- Verify and extract --------------------------------------------------------------------------


@dataclass
class VerifiedBackup:
    manifest: Manifest
    directory: Path  # extracted parts live here; the caller removes it

    @property
    def database(self) -> Path:
        return self.directory / PART_DATABASE

    @property
    def documents(self) -> Path:
        return self.directory / PART_DOCUMENTS

    @property
    def master_key(self) -> Path | None:
        path = self.directory / PART_MASTER_KEY
        return path if self.manifest.includes_master_key and path.exists() else None


def verify_archive(archive: Path, work_dir: Path) -> VerifiedBackup:
    """Extract ``archive`` into ``work_dir`` and verify every part against the manifest.

    Only the expected regular files are accepted; anything else fails verification.
    """
    try:
        tar = tarfile.open(archive, mode="r:gz")  # noqa: SIM115 - closed by the with below
    except (OSError, tarfile.TarError):
        raise BackupError("INVALID_ARCHIVE", "The file is not a Crewquarters backup.") from None
    manifest: Manifest | None = None
    with tar:
        seen: set[str] = set()
        for member in tar:
            if member.name in seen or member.name not in (MANIFEST, *PARTS):
                raise BackupError(
                    "INVALID_ARCHIVE", f"Unexpected entry in the backup: {member.name[:80]!r}"
                )
            if not member.isfile():
                raise BackupError("INVALID_ARCHIVE", f"{member.name} is not a regular file.")
            seen.add(member.name)
            source = tar.extractfile(member)
            assert source is not None
            if member.name == MANIFEST:
                if member.size > 1024 * 1024:
                    raise BackupError("INVALID_MANIFEST", "The backup manifest is too large.")
                manifest = Manifest.from_json(json.loads(source.read()))
                continue
            with _private_file(work_dir / member.name) as out:
                shutil.copyfileobj(source, out, _COPY_CHUNK)
    if manifest is None:
        raise BackupError("INVALID_ARCHIVE", "The backup has no manifest.")
    if manifest.format_version != FORMAT_VERSION:
        raise BackupError(
            "UNSUPPORTED_FORMAT",
            f"Backup format {manifest.format_version} is not supported by this version.",
        )
    required = {PART_DATABASE, PART_DOCUMENTS}
    if manifest.includes_master_key:
        required.add(PART_MASTER_KEY)
    if not required <= set(manifest.parts) or set(manifest.parts) - set(PARTS):
        raise BackupError("INVALID_MANIFEST", "The manifest does not list the expected parts.")
    for part, expected in manifest.parts.items():
        path = work_dir / part
        if not path.is_file():
            raise BackupError("CHECKSUM_MISMATCH", f"{part} is missing from the archive.")
        if path.stat().st_size != int(expected["bytes"]) or sha256_file(path) != expected["sha256"]:
            raise BackupError("CHECKSUM_MISMATCH", f"{part} does not match its checksum.")
    return VerifiedBackup(manifest=manifest, directory=work_dir)


# --- Restore -------------------------------------------------------------------------------------


def other_connections(target: PgTarget) -> int:
    """Sessions connected to the target database other than ours (a running stack)."""
    with target.connect(autocommit=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        ).fetchone()
    return int(row[0]) if row else 0


def restore_database(target: PgTarget, dump: Path, tools: PgTools | None = None) -> None:
    """Replace the database contents with ``dump``: the public schema is dropped and
    recreated, then pg_restore runs in a single transaction and stops at the first error."""
    tools = tools or PgTools()
    with target.connect(autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
    with dump.open("rb") as handle:
        _run_pg(
            [
                *tools.restore,
                f"--dbname={target.dbname}",
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                "--no-password",
            ],
            target,
            stdin=handle,
            code="PG_RESTORE_FAILED",
        )


def _match_owner(root: Path) -> None:
    """Give restored entries the store's owner, and directories its mode (the knowledge
    service must be able to delete files in them)."""
    owner = root.stat()
    for path in root.rglob("*"):
        if path.is_relative_to(root / ".staging"):
            continue
        with contextlib.suppress(OSError):
            if os.geteuid() == 0:
                os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)
            if path.is_dir() and not path.is_symlink():
                path.chmod(owner.st_mode & 0o7777)


def restore_documents(documents_tar: Path, documents_dir: Path) -> int:
    """Replace the document store's contents with the archive's files. In-flight uploads
    (``.staging``) are left alone. Returns the number of files restored."""
    documents_dir.mkdir(parents=True, exist_ok=True)
    staging_new = documents_dir / ".restore-new"
    staging_old = documents_dir / ".restore-old"
    for leftover in (staging_new, staging_old):
        shutil.rmtree(leftover, ignore_errors=True)
    staging_new.mkdir(mode=0o750)
    count = 0
    with tarfile.open(documents_tar, mode="r:") as tar:
        members = [m for m in tar if m.isfile()]
        # The "data" filter rejects absolute paths, "..", links outside the target, and
        # device files, and drops ownership and special mode bits.
        tar.extractall(staging_new, members=members, filter="data")
        count = len(members)
    staging_old.mkdir(mode=0o700)
    for entry in list(documents_dir.iterdir()):
        if entry.name not in DOCUMENTS_SKIP:
            entry.rename(staging_old / entry.name)
    for entry in list(staging_new.iterdir()):
        entry.rename(documents_dir / entry.name)
    shutil.rmtree(staging_new, ignore_errors=True)
    shutil.rmtree(staging_old, ignore_errors=True)
    _match_owner(documents_dir)
    return count


def iter_manifest_summary(manifest: Manifest) -> Iterator[str]:
    """Human-readable lines describing a backup (CLI output)."""
    yield f"created:          {manifest.created_at}"
    yield f"platform version: {manifest.platform_version}"
    yield f"migration head:   {manifest.migration_head}"
    yield f"postgres:         {manifest.postgres_version}"
    docs = manifest.documents
    yield f"documents:        {docs.get('count', 0)} ({docs.get('missing', 0)} missing at backup)"
    yield f"master key:       {'INCLUDED' if manifest.includes_master_key else 'not included'}"
