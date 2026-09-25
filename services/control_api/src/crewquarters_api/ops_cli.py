"""Operations subcommands of ``cq-admin`` (backup, restore, diagnostics, demo reset).

The appliance CLI (``crewquarters backup|diagnostics|demo``) runs these inside a
one-shot platform container; docs/runbooks/backup-restore.md has the operator steps.

cq-admin backup create [--out DIR] [--label L] [--retention N]
                       [--include-master-key [--master-key-file PATH]]
cq-admin backup list [--dir DIR]
cq-admin backup verify FILE
cq-admin backup restore FILE [--documents-dir DIR] [--master-key-file PATH]
                        [--restore-master-key] [--force] --yes
cq-admin diagnostics [--out FILE|-] [--host-tar FILE|-]
cq-admin demo reset --yes [--since ISO] [--schedules] [--knowledge] [--timeout S] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from crewquarters_shared import backup
from crewquarters_shared.config import Settings, get_settings

DEFAULT_MASTER_KEY = "/run/crewquarters-keys/master.key"


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _tools(settings: Settings) -> backup.PgTools:
    return backup.PgTools.from_strings(settings.pg_dump, settings.pg_restore)


def add_parsers(sub: Any) -> None:
    bk = sub.add_parser("backup", help="Create, list, verify or restore backups")
    bsub = bk.add_subparsers(dest="backup_command", required=True)
    create = bsub.add_parser("create", help="Write a backup archive")
    create.add_argument("--out", type=Path, help="Directory (default: CQ_BACKUP_DIR)")
    create.add_argument("--label", help="Suffix for the file name (a-z, 0-9, -)")
    create.add_argument(
        "--retention", type=int, help="Keep only the newest N backups in the directory"
    )
    create.add_argument(
        "--include-master-key",
        action="store_true",
        help="Also store the device master key (sensitive; off by default)",
    )
    create.add_argument(
        "--master-key-file",
        type=Path,
        default=Path(os.environ.get("CQ_MASTER_KEY_FILE", DEFAULT_MASTER_KEY)),
    )
    create.add_argument("--json", action="store_true", help="Print the result as JSON")
    create.add_argument(
        "--owner",
        help="UID:GID to own the manifest and, unless it holds the master key, the archive "
        "(lets the control API list and download device backups)",
    )
    listing = bsub.add_parser("list", help="List backups in a directory")
    listing.add_argument("--dir", type=Path)
    verify = bsub.add_parser("verify", help="Check an archive's checksums")
    verify.add_argument("file", type=Path)
    restore = bsub.add_parser("restore", help="Replace the database and documents")
    restore.add_argument("file", type=Path)
    restore.add_argument("--documents-dir", type=Path)
    restore.add_argument(
        "--master-key-file",
        type=Path,
        default=Path(os.environ.get("CQ_MASTER_KEY_FILE", DEFAULT_MASTER_KEY)),
        help="The device master key: used to check stored connections",
    )
    restore.add_argument(
        "--restore-master-key",
        action="store_true",
        help="Overwrite --master-key-file with the key stored in the backup",
    )
    restore.add_argument(
        "--force", action="store_true", help="Restore even while other sessions are connected"
    )
    restore.add_argument("--yes", action="store_true", help="Confirm replacing all data")

    diag = sub.add_parser("diagnostics", help="Write a redacted diagnostics bundle (zip)")
    diag.add_argument("--out", default="-", help="File, or - for stdout (default)")
    diag.add_argument(
        "--host-tar", help="Tar of host logs/facts to include (redacted); - for stdin"
    )

    demo = sub.add_parser("demo", help="Demo rehearsal helpers")
    dsub = demo.add_subparsers(dest="demo_command", required=True)
    reset = dsub.add_parser("reset", help="Delete rehearsal runs, calls and chats; reseed")
    reset.add_argument("--yes", action="store_true", help="Confirm deleting rehearsal data")
    reset.add_argument("--since", type=datetime.fromisoformat, help="Only data created since")
    reset.add_argument("--schedules", action="store_true", help="Also delete schedules")
    reset.add_argument("--knowledge", action="store_true", help="Also delete knowledge bases")
    reset.add_argument("--timeout", type=float, default=60.0, help="Wait for runs to stop")
    reset.add_argument("--force", action="store_true", help="Delete runs that did not stop")


def _backup_create(args: argparse.Namespace, settings: Settings) -> int:
    out = args.out or settings.backup_dir
    if out is None:
        _err("Pass --out or set CQ_BACKUP_DIR.")
        return 2
    key = None
    if args.include_master_key:
        key = args.master_key_file
        if not key.is_file():
            _err(f"Master key not found at {key}.")
            return 2
        _err(backup.MASTER_KEY_WARNING)
    try:
        result = backup.create_backup(
            out,
            settings.database_url,
            settings.documents_dir,
            tools=_tools(settings),
            platform_version=settings.platform_version,
            master_key_file=key,
            label=args.label,
        )
    except backup.BackupError as exc:
        _err(f"Backup failed [{exc.code}]: {exc.message}")
        return 1
    if args.owner:
        uid, _, gid = args.owner.partition(":")
        owned = [backup.sidecar_path(out, result.name)]
        if key is None:
            owned.append(result.path)
        for path in owned:
            os.chown(path, int(uid), int(gid or uid))
    removed = backup.prune(out, args.retention) if args.retention else []
    if args.json:
        print(
            json.dumps(
                {
                    "name": result.name,
                    "path": str(result.path),
                    "bytes": result.size_bytes,
                    "sha256": result.sha256,
                    "manifest": result.manifest.to_json(),
                    "pruned": removed,
                }
            )
        )
    else:
        print(f"Backup written: {result.path} ({result.size_bytes} bytes, mode 0600)")
        for line in backup.iter_manifest_summary(result.manifest):
            print(f"  {line}")
        for name in removed:
            print(f"  retention: removed {name}")
    if key is not None:
        _err(backup.MASTER_KEY_WARNING)
    return 0


def _backup_list(args: argparse.Namespace, settings: Settings) -> int:
    directory = args.dir or settings.backup_dir
    if directory is None:
        _err("Pass --dir or set CQ_BACKUP_DIR.")
        return 2
    for item in backup.list_backups(directory):
        manifest = item.manifest or {}
        key = " MASTER-KEY" if manifest.get("includesMasterKey") else ""
        print(f"{item.name}  {item.size_bytes:>12}  {manifest.get('migrationHead', '?')}{key}")
    return 0


def _backup_verify(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="cq-verify-") as work:
        try:
            verified = backup.verify_archive(args.file, Path(work))
        except backup.BackupError as exc:
            _err(f"Verification failed [{exc.code}]: {exc.message}")
            return 1
    print(f"{args.file}: checksums OK")
    for line in backup.iter_manifest_summary(verified.manifest):
        print(f"  {line}")
    return 0


def _backup_restore(args: argparse.Namespace, settings: Settings) -> int:
    from crewquarters_api.restore import restore

    if not args.yes:
        _err("Restore replaces ALL platform data with the backup. Re-run with --yes.")
        return 2
    try:
        report = asyncio.run(
            restore(
                args.file,
                settings.database_url,
                args.documents_dir or settings.documents_dir,
                tools=_tools(settings),
                master_key_file=args.master_key_file,
                restore_master_key=args.restore_master_key,
                force=args.force,
                say=lambda line: print(line, flush=True),
            )
        )
    except backup.BackupError as exc:
        _err(f"Restore failed [{exc.code}]: {exc.message}")
        return 3 if exc.code == "STACK_RUNNING" else 1
    for line in report.lines():
        print(line)
    return 0


def _diagnostics(args: argparse.Namespace, settings: Settings) -> int:
    from crewquarters_api import diagnostics

    host_files = None
    if args.host_tar == "-":
        host_files = diagnostics.read_host_tar(sys.stdin.buffer)
    elif args.host_tar:
        with open(args.host_tar, "rb") as handle:
            host_files = diagnostics.read_host_tar(handle)
    bundle = asyncio.run(_diagnostics_bundle(settings, host_files))
    if args.out == "-":
        sys.stdout.buffer.write(bundle)
        sys.stdout.buffer.flush()
    else:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(bundle)
        _err(f"Diagnostics written: {args.out}")
    return 0


async def _diagnostics_bundle(settings: Settings, host_files: dict[str, bytes] | None) -> bytes:
    from crewquarters_api import diagnostics
    from crewquarters_api.main import _runtime_client, _status_clients
    from crewquarters_api.upstream import BrokerClient
    from crewquarters_shared.db import create_engine, session_factory

    token = settings.internal_service_token.get_secret_value()
    broker = BrokerClient(settings.broker_url, token)
    models, connections = _status_clients(settings, broker)
    runtime = _runtime_client(settings)
    engine = create_engine(settings.database_url, 2)
    try:
        return await diagnostics.build_bundle(
            diagnostics.Sources(
                settings=settings,
                sessions=session_factory(engine),
                models=models,
                connections=connections,
                runtime=runtime,
                service="cq-admin",
            ),
            host_files,
        )
    finally:
        await engine.dispose()
        await broker.close()
        close = getattr(models, "close", None)
        if close is not None:
            await close()
        if runtime is not None:
            await runtime.close()


def _demo_reset(args: argparse.Namespace, settings: Settings) -> int:
    from crewquarters_api.demo import ResetBlocked, reset

    if not args.yes:
        _err(
            "Demo reset deletes runs, their events, input requests and calls, and chat "
            "sessions. Re-run with --yes."
        )
        return 2
    try:
        report = asyncio.run(_run_reset(args, settings, reset))
    except ResetBlocked as exc:
        _err(str(exc))
        return 1
    for key, value in report.as_dict().items():
        if key != "warnings":
            print(f"{key}: {value}")
    for warning in report.warnings:
        _err(f"warning: {warning}")
    return 0


async def _run_reset(args: argparse.Namespace, settings: Settings, reset: Any) -> Any:
    from crewquarters_api.gateway_client import GatewayClient
    from crewquarters_api.upstream import ServiceClient
    from crewquarters_shared.db import create_engine, session_factory

    token = settings.internal_service_token.get_secret_value()
    gateway = (
        GatewayClient(
            settings.model_gateway_url,
            token,
            timeout=30.0,
            chat_token=settings.chat_client_token.get_secret_value(),
        )
        if settings.model_gateway_adapter == "http"
        else None
    )
    knowledge = ServiceClient("knowledge", settings.knowledge_url, token)

    async def delete_kb(kb_id: str) -> None:
        await knowledge.request("DELETE", f"/knowledge-bases/{kb_id}")

    engine = create_engine(settings.database_url, 4)
    try:
        return await reset(
            session_factory(engine),
            settings,
            since=args.since,
            include_schedules=args.schedules,
            include_knowledge=args.knowledge,
            cancel_timeout=args.timeout,
            force=args.force,
            release_lease=gateway.release_chat_lease if gateway else None,
            delete_knowledge_base=delete_kb,
            say=lambda line: print(line, flush=True),
        )
    finally:
        await engine.dispose()
        await knowledge.close()
        if gateway is not None:
            await gateway.close()


def dispatch(args: argparse.Namespace) -> int | None:
    """Run an operations command; ``None`` if ``args`` is not one."""
    settings = get_settings()
    if args.command == "backup":
        if args.backup_command == "create":
            return _backup_create(args, settings)
        if args.backup_command == "list":
            return _backup_list(args, settings)
        if args.backup_command == "verify":
            return _backup_verify(args)
        return _backup_restore(args, settings)
    if args.command == "diagnostics":
        return _diagnostics(args, settings)
    if args.command == "demo":
        return _demo_reset(args, settings)
    return None
