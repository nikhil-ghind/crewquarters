"""Load the pinned model catalog (JSON profiles) into ``model_catalog``."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared.db.models_gateway import (
    ModelCatalogEntry,
    ModelInstallation,
    ModelInstance,
)


def read_profiles(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


async def sync(session: AsyncSession, directory: Path) -> list[str]:
    profiles = await asyncio.to_thread(read_profiles, directory)
    for profile in profiles:
        memory = profile.get("memory") or {}
        values = {
            "id": profile["id"],
            "family": profile["family"],
            "display_name": profile["displayName"],
            "backend": profile["backend"],
            "profile": profile,
            "expected_memory_bytes": int(memory.get("startupPeakBytes", 0)),
            "context_limit": int(profile.get("contextLimit", 0)),
            "capabilities": list(profile.get("capabilities", [])),
            "trust_state": (profile.get("validation") or {}).get("status", "untested"),
        }
        await session.execute(
            pg_insert(ModelCatalogEntry)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["id"], set_={k: v for k, v in values.items() if k != "id"}
            )
        )
        await session.execute(
            pg_insert(ModelInstallation).values(model_id=profile["id"]).on_conflict_do_nothing()
        )
        await session.execute(
            pg_insert(ModelInstance).values(model_id=profile["id"]).on_conflict_do_nothing()
        )
    return [p["id"] for p in profiles]
