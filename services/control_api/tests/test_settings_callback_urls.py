"""GET /api/v1/settings reports the two callback origins the broker uses: Google on the
browser's origin, Twilio on CQ_TWILIO_CALLBACK_BASE_URL (docs/adr/0009-callback-base-url.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from crewquarters_api.routers.platform import callback_urls
from crewquarters_shared.config import Settings

BROWSER = "https://crewquarters.local"
TUNNEL = "https://calls.example.net"


@pytest.mark.no_db
def test_twilio_callback_base_falls_back_to_the_public_base() -> None:
    same = Settings(public_base_url=BROWSER + "/")
    assert callback_urls(same) == {
        "googleRedirectUri": f"{BROWSER}/api/v1/connections/google/callback",
        "twilioCallbackBase": f"{BROWSER}/api/v1/callbacks/twilio",
    }
    assert callback_urls(Settings(public_base_url=BROWSER, twilio_callback_base_url="")) == (
        callback_urls(same)
    )
    split = Settings(public_base_url=BROWSER, twilio_callback_base_url=TUNNEL + "/")
    assert callback_urls(split) == {
        "googleRedirectUri": f"{BROWSER}/api/v1/connections/google/callback",
        "twilioCallbackBase": f"{TUNNEL}/api/v1/callbacks/twilio",
    }
    # The tunnel is not a browser origin: only the public base is an allowed origin.
    assert TUNNEL not in split.allowed_origins()


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[Any]:
    from crewquarters_api.main import create_app

    split = settings.model_copy(
        update={"public_base_url": BROWSER, "twilio_callback_base_url": TUNNEL}
    )
    application = create_app(split)
    yield application
    await application.state.engine.dispose()


async def test_settings_report_both_callback_origins(owner: httpx.AsyncClient) -> None:
    got = (await owner.get("/api/v1/settings")).json()
    assert got["callbackBaseUrl"] == BROWSER
    assert got["callbackUrls"] == {
        "googleRedirectUri": f"{BROWSER}/api/v1/connections/google/callback",
        "twilioCallbackBase": f"{TUNNEL}/api/v1/callbacks/twilio",
    }
    patched = await owner.patch("/api/v1/settings", json={"timezone": "Asia/Kolkata"})
    assert patched.status_code == 200
    assert patched.json()["callbackUrls"]["twilioCallbackBase"].startswith(TUNNEL + "/")
