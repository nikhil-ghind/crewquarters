"""Fake platform settings from the environment (as Compose passes them)."""

from __future__ import annotations

import pytest

from crewquarters_fake.settings import FakeSettings


def test_livekit_credentials_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREWQ_FAKE_LIVEKIT_API_KEY", "appliance-key")
    monkeypatch.setenv("CREWQ_FAKE_LIVEKIT_API_SECRET", "owner-provided")
    settings = FakeSettings.from_env()
    assert settings.livekit_api_key == "appliance-key"
    assert settings.livekit_api_secret == "owner-provided"


def test_empty_livekit_credentials_keep_the_development_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Compose passes unset variables through as empty strings.
    monkeypatch.setenv("CREWQ_FAKE_LIVEKIT_API_KEY", "")
    monkeypatch.setenv("CREWQ_FAKE_LIVEKIT_API_SECRET", "")
    settings = FakeSettings.from_env()
    assert settings.livekit_api_key == FakeSettings.livekit_api_key
    assert settings.livekit_api_secret == FakeSettings.livekit_api_secret
