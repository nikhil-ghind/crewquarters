"""Voice tests need a local LiveKit server whose media address matches where the agent runs, so
they are opt-in: the `make voice-e2e*` targets set CREWQ_VOICE_LIVEKIT_URL (and
CREWQ_VOICE_LIVEKIT_MODE for containers). Without it, or without the server, they skip."""

from __future__ import annotations

import os

import httpx
import pytest

from crewquarters_fake.voice.livekit import http_url

LIVEKIT_URL = os.environ.get("CREWQ_VOICE_LIVEKIT_URL", "")


@pytest.fixture(scope="session")
def livekit_url() -> str:
    if not LIVEKIT_URL:
        pytest.skip("voice tests are opt-in; run `make livekit-up && make voice-e2e`")
    try:
        httpx.get(http_url(LIVEKIT_URL), timeout=2).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"no LiveKit server at {LIVEKIT_URL}; run `make voice-up`")
    return LIVEKIT_URL
