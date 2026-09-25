"""Redacted diagnostics bundle (PLAN.md sections 13.13, 15.1 and 23.6).

``GET /api/v1/system/diagnostics`` (owner) and ``cq-admin diagnostics`` build the same
zip. It holds versions, service health and readiness, disk and memory, model states,
connection states, recent failed runs and dead jobs (IDs, states and error codes only;
never payloads, results, prompts or transcripts), the configuration with every secret
masked, and recent logs.

The API process can only read its own logs. ``crewquarters diagnostics`` on the device
adds ``docker compose ps``, every service's recent logs, the runtime daemon's journal and
host facts: it streams them in as a tar (``--host-tar``) and they pass through the same
redaction.

Everything written to the zip passes through :func:`crewquarters_shared.redaction.scrub`
/ :func:`scrub_text` (tokens, phone numbers, emails, API keys, OAuth codes, cookies,
passwords), whatever its source.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import httpx
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_shared.clients import ConnectionStatusClient, ModelStatusClient
from crewquarters_shared.config import Settings
from crewquarters_shared.redaction import scrub, scrub_text
from crewquarters_shared.runtime import RuntimeAdapter
from crewquarters_shared.timeutil import utcnow

MAX_HOST_FILE_BYTES = 20 * 1024 * 1024
MAX_HOST_FILES = 200
FAILED_RUN_LIMIT = 50
HEALTH_TIMEOUT = 3.0
MASK = "********"


@dataclass
class Sources:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession] | None
    models: ModelStatusClient | None = None
    connections: ConnectionStatusClient | None = None
    runtime: RuntimeAdapter | None = None
    log_lines: list[str] = field(default_factory=list)
    service: str = "control-api"
    transport: httpx.AsyncBaseTransport | None = None  # tests


def masked_settings(settings: Settings) -> dict[str, Any]:
    """Settings as JSON with secrets masked (SecretStr fields, URL passwords, and anything
    whose name looks secret)."""
    out: dict[str, Any] = {}
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            out[name] = MASK if value.get_secret_value() else ""
        elif isinstance(value, Path):
            out[name] = str(value)
        else:
            out[name] = value
    masked = scrub(out)
    assert isinstance(masked, dict)
    return masked


async def _guard(label: str, call: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await asyncio.wait_for(call(), timeout=10)
    except Exception as exc:
        return {"error": f"{label} unavailable: {type(exc).__name__}"}


async def _service_health(src: Sources) -> dict[str, Any]:
    s = src.settings
    targets = {
        "capability-broker": f"{s.broker_url.rstrip('/')}/health/ready",
        "knowledge": f"{s.knowledge_url.rstrip('/')}/health/ready",
        "model-gateway": f"{s.model_gateway_url.rstrip('/')}/internal/v1/health",
        "scheduler": s.scheduler_health_url,
    }
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT, transport=src.transport) as http:

        async def probe(name: str, url: str) -> None:
            try:
                response = await http.get(url)
                body: Any
                try:
                    body = response.json()
                except ValueError:
                    body = response.text[:500]
                results[name] = {
                    "status": "ok" if response.status_code < 400 else "unhealthy",
                    "httpStatus": response.status_code,
                    "body": body,
                }
            except httpx.HTTPError as exc:
                results[name] = {"status": "unreachable", "error": type(exc).__name__}

        await asyncio.gather(*(probe(n, u) for n, u in targets.items()))
    return dict(sorted(results.items()))


def _meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree"):
                info[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return info


def _disk(paths: dict[str, Path | None]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, path in paths.items():
        if path is None:
            continue
        try:
            usage = shutil.disk_usage(path)
            out[label] = {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError as exc:
            out[label] = {"error": type(exc).__name__}
    return out


async def _database_facts(src: Sources) -> dict[str, Any]:
    if src.sessions is None:
        return {"error": "database not configured"}
    try:
        async with src.sessions() as db:
            version = await db.scalar(text("SHOW server_version"))
            head = await db.scalar(text("SELECT version_num FROM alembic_version"))
            runs = (
                await db.execute(
                    text(
                        "SELECT r.id, r.state, r.trigger, r.current_attempt, r.error, "
                        "r.created_at, r.finished_at, v.agent_id, v.version "
                        "FROM agent_runs r JOIN agent_versions v ON v.id = r.agent_version_id "
                        "WHERE r.state IN ('FAILED', 'INTERRUPTED') "
                        "ORDER BY r.created_at DESC LIMIT :limit"
                    ),
                    {"limit": FAILED_RUN_LIMIT},
                )
            ).mappings()
            failed_runs = [
                {
                    "id": str(r["id"]),
                    "agent": f"{r['agent_id']}@{r['version']}",
                    "state": r["state"],
                    "trigger": r["trigger"],
                    "attempt": r["current_attempt"],
                    # Error code only: messages and details can quote agent output.
                    "errorCode": (r["error"] or {}).get("code"),
                    "createdAt": r["created_at"],
                    "finishedAt": r["finished_at"],
                }
                for r in runs
            ]
            counts = (
                await db.execute(text("SELECT state, count(*) FROM agent_runs GROUP BY state"))
            ).all()
            jobs = (
                await db.execute(
                    text(
                        "SELECT id, type, state, attempts, last_error, updated_at FROM jobs "
                        "WHERE state = 'dead' ORDER BY updated_at DESC LIMIT 50"
                    )
                )
            ).mappings()
            dead_jobs = [
                {
                    "id": j["id"],
                    "type": j["type"],
                    "state": j["state"],
                    "attempts": j["attempts"],
                    "errorCode": (j["last_error"] or {}).get("code"),
                    "updatedAt": j["updated_at"],
                }
                for j in jobs
            ]
            queue = (
                await db.execute(
                    text("SELECT type, state, count(*) FROM jobs GROUP BY type, state")
                )
            ).all()
        return {
            "postgresVersion": version,
            "migrationHead": head,
            "runCounts": {state: n for state, n in counts},
            "failedRuns": failed_runs,
            "deadJobs": dead_jobs,
            "jobQueue": [{"type": t, "state": s, "count": n} for t, s, n in queue],
        }
    except Exception as exc:
        return {"error": f"database unavailable: {type(exc).__name__}"}


def _models_summary(models: Any) -> Any:
    if not isinstance(models, list):
        return models
    keep = ("id", "displayName", "downloadState", "memoryState", "locality", "lastUsedAt")
    summary = []
    for model in models:
        item = {k: model.get(k) for k in keep if k in model}
        error = model.get("error")
        if isinstance(error, dict):
            item["errorCode"] = error.get("code")
        summary.append(item)
    return summary


def _connections_summary(connections: Any) -> Any:
    if not isinstance(connections, list):
        return connections
    return [
        {
            "provider": c.get("provider"),
            "status": c.get("status"),
            "lastCheckedAt": c.get("lastCheckedAt"),
        }
        for c in connections
    ]


async def collect(src: Sources) -> dict[str, Any]:
    """Every JSON section of the bundle, unredacted (``build_zip`` redacts)."""
    s = src.settings
    models = await _guard("model gateway", src.models.list_models) if src.models else None
    memory = await _guard("model gateway", src.models.memory) if src.models else None
    connections = (
        await _guard("capability broker", src.connections.list_connections)
        if src.connections
        else None
    )
    runtime = await _guard("runtime daemon", src.runtime.capacity) if src.runtime else None
    health, database = await asyncio.gather(_service_health(src), _database_facts(src))
    return {
        "summary.json": {
            "generatedAt": utcnow(),
            "generatedBy": src.service,
            "platformVersion": s.platform_version,
            "profile": s.profile,
            "python": sys.version.split()[0],
            "machine": platform.machine(),
            "kernel": platform.release(),
            "migrationHead": database.get("migrationHead"),
            "postgresVersion": database.get("postgresVersion"),
        },
        "health.json": health,
        "system.json": {
            "memory": _meminfo(),
            "loadAverage": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "disk": _disk(
                {"root": Path("/"), "documents": s.documents_dir, "backups": s.backup_dir}
            ),
            "runtime": runtime,
        },
        "models.json": {"models": _models_summary(models), "memory": memory},
        "connections.json": _connections_summary(connections),
        "runs.json": {
            "counts": database.get("runCounts"),
            "recentFailed": database.get("failedRuns"),
            "error": database.get("error"),
        },
        "jobs.json": {"dead": database.get("deadJobs"), "queue": database.get("jobQueue")},
        "config.json": masked_settings(s),
    }


def _json_bytes(value: Any) -> bytes:
    def default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    return scrub_text(
        json.dumps(scrub(json.loads(json.dumps(value, default=default))), indent=2, sort_keys=True)
    ).encode()


def read_host_tar(stream: IO[bytes]) -> dict[str, bytes]:
    """Regular files from a tar stream (``crewquarters diagnostics``), size-capped."""
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for member in tar:
            if not member.isfile() or len(files) >= MAX_HOST_FILES:
                continue
            raw = Path(member.name)
            if raw.is_absolute() or ".." in raw.parts:
                continue
            name = raw.as_posix().removeprefix("./")
            if not name or name == ".":
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            data = source.read(MAX_HOST_FILE_BYTES + 1)
            if len(data) > MAX_HOST_FILE_BYTES:
                data = data[-MAX_HOST_FILE_BYTES:]  # keep the most recent part of a log
            files[name] = data
    return files


def known_secrets(settings: Settings) -> list[str]:
    """This device's own secret values (keys, tokens, the database password): masked
    literally wherever they appear, whatever surrounds them."""
    values = [
        getattr(settings, name).get_secret_value()
        for name in type(settings).model_fields
        if isinstance(getattr(settings, name), SecretStr)
    ]
    with contextlib.suppress(Exception):
        password = make_url(settings.database_url).password
        if isinstance(password, str):
            values.append(password)
    # Short values would mask ordinary words; the pattern rules still apply to them.
    return sorted({v for v in values if len(v) >= 8}, key=len, reverse=True)


def _mask_known(value: str, secrets: list[str]) -> str:
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    return value


def build_zip(
    sections: dict[str, Any],
    log_lines: list[str],
    host_files: dict[str, bytes] | None = None,
    secrets: list[str] | None = None,
) -> bytes:
    secrets = secrets or []

    def clean(value: str) -> str:
        return scrub_text(_mask_known(value, secrets))

    buffer = io.BytesIO()
    readme = (
        "Crewquarters diagnostics bundle.\n"
        "Secrets, tokens, cookies, OAuth codes, API keys, email addresses and phone numbers\n"
        "are redacted. Runs are listed by id, state and error code only.\n"
    )
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.txt", readme)
        for name, value in sections.items():
            bundle.writestr(name, clean(_json_bytes(value).decode()))
        logs = "\n".join(clean(line) for line in log_lines)
        bundle.writestr("logs/control-api.log", logs + ("\n" if logs else ""))
        for name, data in sorted((host_files or {}).items()):
            bundle.writestr(f"host/{name}", clean(data.decode("utf-8", errors="replace")))
    return buffer.getvalue()


async def build_bundle(src: Sources, host_files: dict[str, bytes] | None = None) -> bytes:
    sections = await collect(src)
    return await asyncio.to_thread(
        build_zip, sections, src.log_lines, host_files, known_secrets(src.settings)
    )


def bundle_filename(now: datetime | None = None) -> str:
    stamp = (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    return f"crewquarters-diagnostics-{stamp}.zip"
