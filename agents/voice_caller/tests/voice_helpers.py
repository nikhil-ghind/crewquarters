"""Shared test helpers for the voice agent (imported as a module; `conftest` is the root one)."""

from __future__ import annotations

from typing import Any

from voice_caller.config import VoiceCallerConfig


def make_config(**overrides: Any) -> VoiceCallerConfig:
    base: dict[str, Any] = {
        "spreadsheetId": "sheet-1",
        "organization": "Acme Dental",
        "purpose": "confirm next week's cleaning appointments",
        "talkingPoints": ["Appointments can be moved online or by phone."],
        "questions": ["Does your appointment time still work?", "Any questions for the dentist?"],
    }
    return VoiceCallerConfig.model_validate({**base, **overrides})
