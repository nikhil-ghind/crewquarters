from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from crewquarters_fake.app import create_app
from crewquarters_fake.settings import FakeSettings


@pytest.fixture
def app() -> FastAPI:
    return create_app(FakeSettings(heartbeat_seconds=0.05))


@pytest.fixture
async def api(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        yield client
