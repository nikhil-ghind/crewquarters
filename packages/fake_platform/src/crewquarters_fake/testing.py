"""Pytest fixtures: a real fake-platform server per test and a client for it."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.server import BackgroundServer
from crewquarters_fake.settings import FakeSettings


@pytest.fixture
def fake_server() -> Iterator[BackgroundServer]:
    with BackgroundServer(create_app(FakeSettings(heartbeat_seconds=0.2))) as server:
        yield server


@pytest.fixture
def fake_client(fake_server: BackgroundServer) -> Iterator[FakePlatformClient]:
    client = FakePlatformClient(fake_server.url)
    yield client
    client.close()
