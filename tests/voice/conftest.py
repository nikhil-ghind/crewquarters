"""Voice tests need a local LiveKit server (make voice-up); they skip cleanly without one."""

from __future__ import annotations

import os

import httpx
import pytest

from crewquarters_fake.voice.livekit import http_url

LIVEKIT_URL = os.environ.get("CREWQ_VOICE_LIVEKIT_URL", "ws://127.0.0.1:7880")


@pytest.fixture(scope="session")
def livekit_url() -> str:
    try:
        httpx.get(http_url(LIVEKIT_URL), timeout=2).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"no LiveKit server at {LIVEKIT_URL}; run `make voice-up`")
    return LIVEKIT_URL
