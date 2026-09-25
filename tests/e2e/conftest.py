"""E2E fixtures: the Compose fake platform and pinned agent manifests produced by `make e2e`."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from crewquarters_fake.client import FakePlatformClient

REPO = Path(__file__).resolve().parents[2]
PINNED = REPO / ".e2e" / "manifests"
# `make fake-up` publishes the fake platform on 8090 (8080 is the real control API).
PLATFORM_URL = os.environ.get("CREWQ_E2E_PLATFORM_URL", "http://127.0.0.1:8090")


@pytest.fixture(scope="session")
def stack() -> str:
    try:
        httpx.get(f"{PLATFORM_URL}/health/ready", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("the fake platform is not running; use `make fake-up && make e2e`")
    for agent in ("contract_probe", "gmail_digest", "caller"):
        if not (PINNED / f"{agent}.yaml").is_file():
            pytest.skip(
                "pinned manifests are missing; run `make e2e` to build and push agent images"
            )
    return PLATFORM_URL


@pytest.fixture
def platform(stack: str) -> Iterator[FakePlatformClient]:
    client = FakePlatformClient(stack)
    client.reset()
    yield client
    client.close()
