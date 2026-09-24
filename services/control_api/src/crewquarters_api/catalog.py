"""Curated local catalog: sync bundled manifests from disk and import developer builds."""

from __future__ import annotations

import asyncio
import logging
import platform
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared.db.models import AgentCatalogEntry, AgentVersion
from crewquarters_shared.errors import PlatformError, conflict
from crewquarters_shared.manifest import image_digest, load_manifest_file, validate_manifest

log = logging.getLogger(__name__)

_ARCH = {
    "x86_64": "linux/amd64",
    "amd64": "linux/amd64",
    "aarch64": "linux/arm64",
    "arm64": "linux/arm64",
}


def host_architecture() -> str:
    return _ARCH.get(platform.machine().lower(), f"linux/{platform.machine().lower()}")


def compatibility_issues(manifest: dict[str, Any]) -> list[str]:
    issues = []
    arch = host_architecture()
    if arch not in manifest["spec"]["architectures"]:
        issues.append(f"This agent has no {arch} image for this device.")
    if manifest["spec"].get("sdkProtocol", "v1alpha1") != "v1alpha1":
        issues.append("This agent needs a newer platform SDK protocol.")
    return issues


async def upsert_manifest(
    session: AsyncSession, manifest: dict[str, Any], *, source: str
) -> AgentVersion:
    """Insert a catalog entry/version. Versions are immutable: re-importing the same
    version with a different manifest is rejected."""
    manifest = validate_manifest(manifest)
    meta, spec = manifest["metadata"], manifest["spec"]
    trust = "curated" if source == "bundled" else "imported_unreviewed"

    entry = await session.get(AgentCatalogEntry, meta["id"])
    if entry is not None and entry.source != source:
        raise conflict(
            "CATALOG_SOURCE_CONFLICT",
            f"{meta['id']} already exists as a {entry.source} agent.",
            agentId=meta["id"],
        )
    existing = await session.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == meta["id"], AgentVersion.version == meta["version"]
        )
    )
    if existing is not None:
        if existing.manifest != manifest:
            raise conflict(
                "AGENT_VERSION_IMMUTABLE",
                f"{meta['id']} {meta['version']} already exists with a different manifest.",
                agentId=meta["id"],
                version=meta["version"],
            )
        return existing

    await session.execute(
        pg_insert(AgentCatalogEntry)
        .values(
            agent_id=meta["id"],
            name=meta["name"],
            summary=meta.get("summary", ""),
            publisher=meta.get("publisher", "local"),
            current_version=meta["version"],
            source=source,
            trust_status=trust,
        )
        .on_conflict_do_update(
            index_elements=["agent_id"],
            set_={
                "name": meta["name"],
                "summary": meta.get("summary", ""),
                "publisher": meta.get("publisher", "local"),
                "current_version": meta["version"],
            },
        )
    )
    version = AgentVersion(
        agent_id=meta["id"],
        version=meta["version"],
        manifest=manifest,
        image_ref=spec["image"],
        image_digest=image_digest(spec["image"]),
        sdk_protocol=spec["sdkProtocol"],
        architectures=list(spec["architectures"]),
    )
    session.add(version)
    await session.flush()
    return version


async def sync_directory(session: AsyncSession, directory: Path) -> list[str]:
    """Load ``*.yaml`` manifests from the bundled catalog directory."""
    loaded: list[str] = []
    paths = await asyncio.to_thread(lambda: sorted(directory.glob("*.yaml")))
    for path in paths:
        try:
            manifest = await asyncio.to_thread(load_manifest_file, path)
            async with session.begin_nested():
                version = await upsert_manifest(session, manifest, source="bundled")
            loaded.append(f"{version.agent_id}@{version.version}")
        except PlatformError as exc:
            log.error("catalog manifest %s rejected: %s %s", path.name, exc.code, exc.details)
    return loaded
