from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text

from conftest import ORIGIN, PASSWORD, issue_bootstrap_token
from crewquarters_shared.db.models import AuditEvent, Session, User


async def test_bootstrap_creates_owner_once(client: httpx.AsyncClient, sessions) -> None:
    token = await issue_bootstrap_token(sessions)
    body = {"token": token, "username": "Owner", "password": PASSWORD}
    first = await client.post("/api/v1/bootstrap", json=body)
    assert first.status_code == 201, first.text
    data = first.json()
    assert data["user"]["role"] == "owner"
    assert data["csrfToken"]
    cookie = first.headers.get_list("set-cookie")
    assert any("cq_session=" in c and "HttpOnly" in c and "SameSite=lax" in c for c in cookie)

    again = await client.post("/api/v1/bootstrap", json=body)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ALREADY_BOOTSTRAPPED"

    async with sessions() as db:
        user = await db.scalar(select(User))
        assert user.password_hash.startswith("$argon2id$")
        assert (
            await db.scalar(text("SELECT count(*) FROM settings WHERE key = 'bootstrap.token'"))
            == 0
        )


async def test_bootstrap_rejects_wrong_token(client: httpx.AsyncClient, sessions) -> None:
    await issue_bootstrap_token(sessions)
    response = await client.post(
        "/api/v1/bootstrap", json={"token": "x" * 43, "username": "owner", "password": PASSWORD}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_BOOTSTRAP_TOKEN"
    assert response.json()["error"]["requestId"]
    async with sessions() as db:
        denied = await db.scalar(select(AuditEvent).where(AuditEvent.outcome == "denied"))
        assert denied.action == "auth.bootstrap"


async def test_login_logout_and_session_hash_only(owner: httpx.AsyncClient, sessions) -> None:
    await owner.delete("/api/v1/sessions/current")
    assert (await owner.get("/api/v1/me")).status_code == 401

    bad = await owner.post("/api/v1/sessions", json={"username": "owner", "password": "wrong"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_CREDENTIALS"

    good = await owner.post("/api/v1/sessions", json={"username": "OWNER", "password": PASSWORD})
    assert good.status_code == 201
    token = owner.cookies.get("cq_session")
    async with sessions() as db:
        stored = [s.token_hash for s in (await db.scalars(select(Session))).all()]
    assert token not in stored and len(stored) == 2
    me = await owner.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "owner"


async def test_state_change_requires_csrf_and_origin(owner: httpx.AsyncClient) -> None:
    csrf = owner.headers.pop("X-CSRF-Token")
    no_csrf = await owner.patch("/api/v1/settings", json={"timezone": "Asia/Kolkata"})
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_FAILED"

    owner.headers["X-CSRF-Token"] = csrf
    evil = await owner.patch(
        "/api/v1/settings",
        json={"timezone": "Asia/Kolkata"},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403
    assert evil.json()["error"]["code"] == "ORIGIN_REJECTED"

    ok = await owner.patch("/api/v1/settings", json={"timezone": "Asia/Kolkata"})
    assert ok.status_code == 200
    assert ok.json()["timezone"] == "Asia/Kolkata"
    assert ok.headers["x-content-type-options"] == "nosniff"


async def test_expired_session_is_rejected(owner: httpx.AsyncClient, sessions) -> None:
    async with sessions() as db:
        await db.execute(text("UPDATE sessions SET idle_expires_at = now() - interval '1 second'"))
        await db.commit()
    response = await owner.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"


async def test_login_is_rate_limited(app, client: httpx.AsyncClient) -> None:
    app.state.cq.auth_limiter.limit = 3
    codes = [
        (await client.post("/api/v1/sessions", json={"username": "x", "password": "y"})).status_code
        for _ in range(4)
    ]
    assert codes == [401, 401, 401, 429]


async def test_audit_events_are_append_only(owner: httpx.AsyncClient, sessions) -> None:
    async with sessions() as db:
        with pytest.raises(Exception, match="immutable"):
            await db.execute(text("UPDATE audit_events SET action = 'tampered'"))
        await db.rollback()
    events = await owner.get("/api/v1/audit-events", params={"action": "auth.*"})
    assert events.status_code == 200
    assert [e["action"] for e in events.json()["items"]] == ["auth.bootstrap"]


async def test_unauthenticated_requests_rejected(client: httpx.AsyncClient) -> None:
    for path in ["/api/v1/me", "/api/v1/runs", "/api/v1/schedules", "/api/v1/catalog/agents"]:
        response = await client.get(path)
        assert response.status_code == 401, path


async def test_internal_routes_require_service_token(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/internal/v1/runs/{'0' * 8}-0000-0000-0000-000000000000")
    assert response.status_code == 401
    response = await client.get(
        "/internal/v1/runs/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer wrong", "Origin": ORIGIN},
    )
    assert response.status_code == 401
