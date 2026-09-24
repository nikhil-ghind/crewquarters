"""Tests for PLAN.md requirements added during the spec-conformance review."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import httpx
import pytest
import yaml
from sqlalchemy import text

from conftest import HELLO_PERMISSIONS, ROOT, install_hello
from crewquarters_scheduler.main import serve_metrics
from crewquarters_scheduler.worker import Worker
from crewquarters_shared.logs import JsonFormatter
from crewquarters_shared.metrics import SchedulerMetrics
from crewquarters_shared.timeutil import utcnow

pytestmark = pytest.mark.usefixtures("catalog_synced")

SERVICE = {"Authorization": "Bearer dev-insecure-internal-token-change-me-0000"}


class InertRuntime:
    async def start_run(self, spec):  # type: ignore[no-untyped-def]
        return f"inert-{spec.run_id}-{spec.attempt}"

    async def stop_run(self, ref, grace_seconds):  # type: ignore[no-untyped-def]
        return None


async def drain(sessions, settings) -> None:  # type: ignore[no-untyped-def]
    worker = Worker(sessions, InertRuntime(), settings, worker_id="t")  # type: ignore[arg-type]
    while await worker.run_once():
        pass


async def test_every_list_endpoint_is_paginated(owner: httpx.AsyncClient) -> None:
    await install_hello(owner)
    for path in [
        "/api/v1/catalog/agents",
        "/api/v1/agent-installations",
        "/api/v1/runs",
        "/api/v1/input-requests",
        "/api/v1/schedules",
        "/api/v1/models",
        "/api/v1/connections",
        "/api/v1/audit-events",
    ]:
        body = (await owner.get(path)).json()
        assert set(body) == {"items", "nextCursor"}, path
    first = (await owner.get("/api/v1/connections", params={"limit": 3})).json()
    rest = (
        await owner.get("/api/v1/connections", params={"limit": 3, "cursor": first["nextCursor"]})
    ).json()
    providers = [c["provider"] for c in first["items"] + rest["items"]]
    assert providers == ["anthropic", "google", "openai", "twilio"]
    assert rest["nextCursor"] is None


async def test_request_body_size_limit(owner: httpx.AsyncClient) -> None:
    big = {"manifest": {"blob": "x" * (3 * 1024 * 1024)}}
    declared = await owner.post("/api/v1/catalog/agents/import", json=big)
    assert declared.status_code == 413
    assert declared.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(40):
            yield b"x" * 100_000

    chunked = await owner.post(
        "/api/v1/catalog/agents/import",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert chunked.status_code == 413


async def test_patch_and_delete_accept_idempotency_keys(owner: httpx.AsyncClient) -> None:
    installation = await install_hello(owner)
    body = {"version": installation["version"], "enabled": False}
    headers = {"Idempotency-Key": "disable-once-001"}
    url = f"/api/v1/agent-installations/{installation['id']}"
    first = await owner.patch(url, json=body, headers=headers)
    again = await owner.patch(url, json=body, headers=headers)
    assert first.status_code == again.status_code == 200
    assert again.headers["idempotent-replayed"] == "true"
    assert again.json()["version"] == first.json()["version"] == installation["version"] + 1

    delete_headers = {"Idempotency-Key": "uninstall-once-01"}
    gone = await owner.delete(url, headers=delete_headers)
    replay = await owner.delete(url, headers=delete_headers)
    assert gone.status_code == replay.status_code == 204
    assert replay.headers["idempotent-replayed"] == "true"


async def test_setup_state_is_saved_server_side(owner: httpx.AsyncClient) -> None:
    state = {"step": "connections", "completed": ["preflight", "owner", "storage"]}
    saved = await owner.patch("/api/v1/settings", json={"setupState": state})
    assert saved.status_code == 200
    assert (await owner.get("/api/v1/settings")).json()["setupState"] == state


async def test_attention_lists_questions_failures_and_blocked_schedules(
    owner: httpx.AsyncClient, sessions, settings
) -> None:  # type: ignore[no-untyped-def]
    assert (await owner.get("/api/v1/attention")).json() == {"count": 0, "items": []}

    asking = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": asking["id"]})).json()
    await drain(sessions, settings)
    base = f"/internal/v1/runs/{run['id']}"
    await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    ask = {"key": "k", "title": "Approve?", "prompt": "p", "schema": {"type": "boolean"}}
    await owner.post(
        f"{base}/input-requests", json={"attempt": 1, "timeoutSeconds": 60, **ask}, headers=SERVICE
    )

    failing = await install_hello(owner)
    failed = (await owner.post("/api/v1/runs", json={"installationId": failing["id"]})).json()
    await drain(sessions, settings)
    fbase = f"/internal/v1/runs/{failed['id']}"
    await owner.post(f"{fbase}/handshake", json={"attempt": 1}, headers=SERVICE)
    await owner.post(
        f"{fbase}/result",
        json={"attempt": 1, "status": "failed", "error": {"code": "BOOM", "message": "x"}},
        headers=SERVICE,
    )

    blocked = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": {"modelProfile": "local.general.quality"},
            "approvedPermissions": HELLO_PERMISSIONS,
            "modelBindings": {"local.general": "local.general.quality"},
        },
    )
    await owner.post(
        "/api/v1/schedules",
        json={"installationId": blocked.json()["id"], "cron": "0 10 * * *", "timezone": "UTC"},
    )

    attention = (await owner.get("/api/v1/attention")).json()
    kinds = sorted(i["kind"] for i in attention["items"])
    assert kinds == ["failed_run", "input_request", "schedule_blocked"]
    assert attention["count"] == 3

    ack = await owner.post(f"/api/v1/runs/{failed['id']}/acknowledge")
    assert ack.status_code == 200 and ack.json()["acknowledgedAt"]
    kinds = sorted(i["kind"] for i in (await owner.get("/api/v1/attention")).json()["items"])
    assert kinds == ["input_request", "schedule_blocked"]
    not_done = await owner.post(f"/api/v1/runs/{run['id']}/acknowledge")
    assert not_done.status_code == 409


async def test_agents_without_user_input_permission_cannot_ask(
    owner: httpx.AsyncClient, sessions, settings
) -> None:  # type: ignore[no-untyped-def]
    manifest = yaml.safe_load((ROOT / "catalog/dev/hello-crew.yaml").read_text())
    manifest["metadata"]["id"] = "silent-crew"
    manifest["spec"]["permissions"]["userInput"] = False
    assert (
        await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    ).status_code == 201
    installation = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "silent-crew",
            "config": {},
            "approvedPermissions": manifest["spec"]["permissions"],
        },
    )
    run = (
        await owner.post("/api/v1/runs", json={"installationId": installation.json()["id"]})
    ).json()
    await drain(sessions, settings)
    base = f"/internal/v1/runs/{run['id']}"
    await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    denied = await owner.post(
        f"{base}/input-requests",
        json={
            "attempt": 1,
            "key": "k",
            "title": "t",
            "prompt": "p",
            "schema": {},
            "timeoutSeconds": 60,
        },
        headers=SERVICE,
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_internal_run_exposes_current_capability_token_id(
    owner: httpx.AsyncClient, sessions, settings
) -> None:  # type: ignore[no-untyped-def]
    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await drain(sessions, settings)
    status = (await owner.get(f"/internal/v1/runs/{run['id']}", headers=SERVICE)).json()
    async with sessions() as db:
        jti = await db.scalar(text("SELECT capability_token_id FROM run_attempts"))
    assert status["capabilityTokenId"] == jti and len(jti) == 32


async def test_api_metrics_endpoint(owner: httpx.AsyncClient) -> None:
    await install_hello(owner)
    assert (await owner.get("/internal/v1/metrics")).status_code == 401
    response = await owner.get("/internal/v1/metrics", headers=SERVICE)
    assert response.status_code == 200
    body = response.text
    assert (
        'cq_http_requests_total{method="POST",route="/api/v1/agent-installations",status="201"}'
        in body
    )
    assert "cq_http_request_duration_seconds_bucket" in body
    assert "cq_job_oldest_available_age_seconds" in body
    assert "cq_input_requests_pending" in body


async def test_scheduler_metrics_and_health_endpoint(settings) -> None:  # type: ignore[no-untyped-def]
    metrics = SchedulerMetrics()
    metrics.ticks.inc()
    server = await serve_metrics(settings.model_copy(update={"scheduler_metrics_port": 0}), metrics)
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            assert (await client.get("/health")).json() == {"status": "ok"}
            assert "cq_scheduler_ticks_total 1.0" in (await client.get("/metrics")).text
            assert (await client.get("/nope")).status_code == 404
    finally:
        server.close()


@pytest.mark.no_db
def test_json_logs_are_structured_and_redacted() -> None:
    formatter = JsonFormatter("control-api")
    record = logging.makeLogRecord(
        {
            "name": "t",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "calling %s with Bearer abc.def",
            "args": ("+14155550100",),
            "request_id": "req-123",
            "run_id": "run-1",
            "event": "http.request",
            "apiKey": "sk-secret",
        }
    )
    entry = json.loads(formatter.format(record))
    assert entry["service"] == "control-api" and entry["level"] == "info"
    assert entry["request_id"] == "req-123" and entry["run_id"] == "run-1"
    assert entry["message"] == "calling ***0100 with Bearer [REDACTED]"
    assert entry["apiKey"] == "[REDACTED]"


async def test_online_schedule_dispatches_within_five_seconds(
    owner: httpx.AsyncClient, sessions, platform
) -> None:  # type: ignore[no-untyped-def]
    installation = await install_hello(owner)
    schedule = (
        await owner.post(
            "/api/v1/schedules",
            json={"installationId": installation["id"], "cron": "* * * * *", "timezone": "UTC"},
        )
    ).json()
    due = utcnow() + timedelta(seconds=1)
    async with sessions() as db, db.begin():
        await db.execute(
            text("UPDATE schedules SET next_run_at = :due WHERE id = :id"),
            {"due": due, "id": schedule["id"]},
        )
    for _ in range(100):
        runs = (await owner.get("/api/v1/runs", params={"trigger": "schedule"})).json()["items"]
        if runs:
            break
        await asyncio.sleep(0.1)
    assert runs, "scheduled run was not created"
    async with sessions() as db:
        lag = await db.scalar(
            text("SELECT extract(epoch FROM created_at - scheduled_for) FROM agent_runs")
        )
    assert 0 <= float(lag) <= 5.0
