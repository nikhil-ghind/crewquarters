from __future__ import annotations

import pytest
from voice_helpers import make_config

from voice_caller.config import VoiceCallerConfig


@pytest.fixture
def config() -> VoiceCallerConfig:
    return make_config()
