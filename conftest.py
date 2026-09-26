"""Shared test fixtures: an isolated, migrated PostgreSQL database per test session.

Set CQ_TEST_ADMIN_URL to point at a PostgreSQL 16 + pgvector server
(default: the dev Compose database on 127.0.0.1:55432).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from crewquarters_shared.config import Settings
from crewquarters_shared.db import create_engine, session_factory

ROOT = Path(__file__).parent

# Fake-platform fixtures (fake_server, fake_client) for the SDK, crewctl, and agent suites.
pytest_plugins = ["crewquarters_fake.testing", "gateway_helpers"]

# Suites that never touch PostgreSQL. They are marked ``no_db`` so the autouse ``clean_db``
# fixture below skips them and they run without the database.
DB_FREE_ROOTS = (
    "packages/python_sdk",
    "packages/fake_platform",
    "packages/crewctl",
    "agents",
    "tests/integration",
    "tests/e2e",
    "tests/live",
    "tests/voice",
    "packages/speech_server",
    "tests/contract/test_broker_contract_files.py",
    "tests/contract/test_fake_route_parity.py",
    "tests/contract/test_fake_traffic_conformance.py",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        try:
            relative = item.path.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if relative.startswith(DB_FREE_ROOTS):
            item.add_marker(pytest.mark.no_db)


ADMIN_URL = os.environ.get(
    "CQ_TEST_ADMIN_URL",
    "postgresql+psycopg://crewquarters:crewquarters@127.0.0.1:55432/crewquarters",
)
ORIGIN = "http://localhost:8080"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "services/control_api/alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@contextmanager
def fresh_database() -> Iterator[str]:
    """Create a brand-new empty database and drop it afterwards."""
    name = f"cq_test_{uuid.uuid4().hex[:10]}"
    admin = create_sync_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def alembic_config(url: str) -> Config:
    return _alembic_config(url)


@pytest.fixture(scope="session")
def empty_database_url() -> Iterator[str]:
    with fresh_database() as url:
        yield url
        for cached in list(_SYNC_ENGINES):
            if cached == url:
                _SYNC_ENGINES.pop(cached).dispose()


@pytest.fixture(scope="session")
def database_url(empty_database_url: str) -> str:
    command.upgrade(_alembic_config(empty_database_url), "head")
    return empty_database_url


@pytest.fixture(scope="session")
def settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        catalog_dir=None,
        public_origins=[ORIGIN],
        heartbeat_timeout_seconds=2,
        prepare_timeout_seconds=5,
        job_lease_seconds=2,
        worker_concurrency=2,
        auth_rate_limit_per_minute=1000,
        fake_connections=["google", "twilio", "openai", "anthropic"],
        model_gateway_adapter="fake",
    )


@pytest.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(settings.database_url, pool_size=10)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return session_factory(engine)


_SYNC_ENGINES: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def clean_db(request: pytest.FixtureRequest) -> Iterator[None]:
    """Truncate every table before each database test (synchronous, so it never needs
    the async engine from inside a running event loop)."""
    if "no_db" in request.keywords:
        yield
        return
    url = request.getfixturevalue("database_url")
    engine = _SYNC_ENGINES.get(url)
    if engine is None:
        engine = _SYNC_ENGINES[url] = create_sync_engine(url)
    with engine.begin() as conn:
        tables = (
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        if tables:
            conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[Any]:
    from crewquarters_api.main import create_app

    application = create_app(settings)
    yield application
    await application.state.engine.dispose()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as c:
        yield c


async def issue_bootstrap_token(sessions: async_sessionmaker[AsyncSession]) -> str:
    from datetime import timedelta

    from crewquarters_api import security
    from crewquarters_api.routers.auth import BOOTSTRAP_SETTING
    from crewquarters_shared.db.models import Setting
    from crewquarters_shared.timeutil import utcnow

    token = security.new_token()
    async with sessions() as db:
        db.add(
            Setting(
                key=BOOTSTRAP_SETTING,
                value={
                    "hash": security.hash_token(token),
                    "expiresAt": (utcnow() + timedelta(hours=1)).isoformat(),
                },
                value_type="object",
                version=1,
            )
        )
        await db.commit()
    return token


@pytest.fixture
async def owner(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> httpx.AsyncClient:
    """The client, signed in as the bootstrapped owner with the CSRF header set."""
    token = await issue_bootstrap_token(sessions)
    response = await client.post(
        "/api/v1/bootstrap", json={"token": token, "username": "owner", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
    return client


@pytest.fixture
async def catalog_synced(sessions: async_sessionmaker[AsyncSession]) -> None:
    from crewquarters_api.catalog import sync_directory

    async with sessions() as db:
        loaded = await sync_directory(db, ROOT / "catalog/dev")
        await db.commit()
    assert loaded == ["hello-crew@0.1.0"]


HELLO_PERMISSIONS = {
    "llmProfiles": ["local.general"],
    "knowledge": [],
    "connectors": {},
    "cloudProviders": [],
    "userInput": True,
}


async def install_hello(
    owner: httpx.AsyncClient, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": config or {},
            "approvedPermissions": HELLO_PERMISSIONS,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class RunningPlatform:
    """Worker + scheduler + reconciler loops running in the test's event loop."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        import asyncio

        from crewquarters_scheduler.worker import Worker
        from crewquarters_shared.fakes.runtime import FakeRuntime

        self.sessions = sessions
        self.settings = settings
        self.runtime = FakeRuntime(
            sessions, heartbeat_seconds=settings.heartbeat_timeout_seconds, step_seconds=0.05
        )
        self.worker = Worker(sessions, self.runtime, settings, worker_id="test-worker")
        self.stop = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []

    async def _control_loop(self) -> None:
        import asyncio
        import contextlib

        from crewquarters_scheduler import exits, reconciler, scheduler
        from crewquarters_shared.timeutil import utcnow

        while not self.stop.is_set():
            async with self.sessions() as session, session.begin():
                await scheduler.tick(session, utcnow(), self.settings.misfire_grace_seconds)
            async with self.sessions() as session, session.begin():
                await reconciler.tick(session, utcnow())
            await exits.tick(self.sessions, self.runtime, utcnow())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=0.1)

    async def __aenter__(self) -> RunningPlatform:
        import asyncio

        self.tasks = [
            asyncio.create_task(self.worker.run_forever(self.stop)),
            asyncio.create_task(self._control_loop()),
        ]
        return self

    async def __aexit__(self, *exc: object) -> None:
        import asyncio

        self.stop.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.runtime.close()


@pytest.fixture
async def platform(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> AsyncIterator[RunningPlatform]:
    async with RunningPlatform(sessions, settings) as running:
        yield running


async def wait_for_state(
    owner: httpx.AsyncClient, run_id: str, states: set[str], within: float = 10.0
) -> dict[str, Any]:
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while True:
        run = (await owner.get(f"/api/v1/runs/{run_id}")).json()
        if run["state"] in states:
            return dict(run)
        if loop.time() > deadline:
            raise AssertionError(f"run stuck in {run['state']}, wanted {states}: {run}")
        await asyncio.sleep(0.05)
