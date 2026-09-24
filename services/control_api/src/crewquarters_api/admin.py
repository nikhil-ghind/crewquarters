"""``cq-admin``: installer and operator commands.

cq-admin bootstrap-token [--ttl-hours 24]   print a new one-time owner setup code
cq-admin catalog-sync [DIR]                 load bundled manifests into the catalog
cq-admin export-openapi PATH                write the OpenAPI document (YAML)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from crewquarters_api import catalog, security
from crewquarters_api.routers.auth import BOOTSTRAP_SETTING
from crewquarters_shared import audit
from crewquarters_shared.config import get_settings
from crewquarters_shared.db import create_engine, session_factory
from crewquarters_shared.db.models import Setting, User
from crewquarters_shared.timeutil import utcnow


async def _bootstrap_token(ttl_hours: int) -> str | None:
    settings = get_settings()
    engine = create_engine(settings.database_url, 2)
    try:
        async with session_factory(engine)() as db:
            if await db.scalar(select(func.count()).select_from(User)):
                return None
            token = security.new_token()
            value = {
                "hash": security.hash_token(token),
                "expiresAt": (utcnow() + timedelta(hours=ttl_hours)).isoformat(),
            }
            row = await db.get(Setting, BOOTSTRAP_SETTING, with_for_update=True)
            if row is None:
                db.add(Setting(key=BOOTSTRAP_SETTING, value=value, value_type="object", version=1))
            else:
                row.value = value
                row.version += 1
            audit.record(db, action="auth.bootstrap_token_issued", actor_type="system")
            await db.commit()
            return token
    finally:
        await engine.dispose()


async def _catalog_sync(directory: Path) -> list[str]:
    settings = get_settings()
    engine = create_engine(settings.database_url, 2)
    try:
        async with session_factory(engine)() as db:
            loaded = await catalog.sync_directory(db, directory)
            await db.commit()
            return loaded
    finally:
        await engine.dispose()


def export_openapi(path: Path) -> None:
    from crewquarters_api.openapi import render_openapi

    path.write_text(render_openapi())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cq-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    token = sub.add_parser("bootstrap-token", help="Issue a one-time owner setup code")
    token.add_argument("--ttl-hours", type=int, default=24)
    sync = sub.add_parser("catalog-sync", help="Load bundled agent manifests")
    sync.add_argument("directory", nargs="?", type=Path)
    export = sub.add_parser("export-openapi", help="Write the OpenAPI YAML")
    export.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    if args.command == "bootstrap-token":
        issued = asyncio.run(_bootstrap_token(args.ttl_hours))
        if issued is None:
            print("The owner account already exists; no setup code issued.", file=sys.stderr)
            return 1
        print(issued)
        return 0
    if args.command == "catalog-sync":
        directory = args.directory or get_settings().catalog_dir
        if directory is None:
            print("Pass a directory or set CQ_CATALOG_DIR.", file=sys.stderr)
            return 2
        for item in asyncio.run(_catalog_sync(directory)):
            print(item)
        return 0
    export_openapi(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
