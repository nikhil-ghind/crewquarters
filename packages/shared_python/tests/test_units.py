from __future__ import annotations

import time
import uuid

import jwt
import pytest

from crewquarters_shared import capability
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.ids import uuid7
from crewquarters_shared.manifest import resolve_model_bindings
from crewquarters_shared.redaction import mask_phone, redact

pytestmark = pytest.mark.no_db


def test_uuid7_is_versioned_and_time_ordered() -> None:
    ids = []
    for _ in range(5):
        ids.append(uuid7())
        time.sleep(0.002)
    assert all(i.version == 7 and i.variant == uuid.RFC_4122 for i in ids)
    assert ids == sorted(ids)


def test_capability_token_round_trip_and_tamper() -> None:
    caps = capability.capabilities_from_permissions(
        {
            "llmProfiles": ["local.general"],
            "knowledge": ["config"],
            "connectors": {"google": ["gmail.readonly"], "twilio": ["call.fixed_script"]},
            "cloudProviders": ["openai"],
            "userInput": True,
        },
        {"local.general": "local.general.quality"},
    )
    assert caps == [
        "cloud.openai",
        "events.write",
        "google.gmail.readonly",
        "idempotency",
        "knowledge.search:config",
        "llm.profile:local.general.quality",
        "twilio.call.fixed_script",
        "user_input",
    ]
    run_id, inst, ver = uuid7(), uuid7(), uuid7()
    token, claims = capability.mint(
        signing_key="k" * 32,
        run_id=run_id,
        attempt=2,
        installation_id=inst,
        agent_version_id=ver,
        capabilities=caps,
        ttl_seconds=60,
    )
    verified = capability.verify(token, "k" * 32)
    assert verified.run_id == str(run_id) and verified.attempt == 2
    assert verified.capabilities == caps and verified.token_id == claims.token_id
    with pytest.raises(jwt.InvalidSignatureError):
        capability.verify(token, "x" * 32)
    expired, _ = capability.mint(
        signing_key="k" * 32,
        run_id=run_id,
        attempt=1,
        installation_id=inst,
        agent_version_id=ver,
        capabilities=[],
        ttl_seconds=-10,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        capability.verify(expired, "k" * 32)


def test_profile_resolution_rules() -> None:
    assert resolve_model_bindings(["local.general"]) == {"local.general": "local.general.small"}
    assert resolve_model_bindings(
        ["local.general"], {"local.general": "local.general.quality"}
    ) == {"local.general": "local.general.quality"}
    assert resolve_model_bindings(["local.general.quality", "openai.default"]) == {
        "local.general.quality": "local.general.quality",
        "openai.default": "openai.default",
    }
    for requested, choice in [
        (["local.general"], {"local.general": "local.embedding.small"}),
        (["local.general.small"], {"local.general.small": "local.general.quality"}),
        (["local.general"], {"local.other": "x"}),
    ]:
        with pytest.raises(PlatformError) as err:
            resolve_model_bindings(requested, choice)
        assert err.value.code == "INVALID_MODEL_BINDING"


def test_redaction() -> None:
    data = {
        "refreshToken": "abc",
        "apiKey": "sk-123",
        "note": "call +919876543210 now",
        "headers": {"Authorization": "Bearer abc.def"},
        "errorCode": "KEEP",
        "items": [{"password": "p"}],
    }
    out = redact(data)
    assert out["refreshToken"] == out["apiKey"] == "[REDACTED]"
    assert out["note"] == "call ***3210 now"
    assert out["headers"]["Authorization"] == "[REDACTED]"
    assert out["errorCode"] == "KEEP"
    assert out["items"][0]["password"] == "[REDACTED]"
    assert mask_phone("+1 (415) 555-0100") == "***0100"


def test_allowed_origins_include_the_public_base_url() -> None:
    from crewquarters_shared.config import Settings

    settings = Settings(
        public_origins=["http://localhost:8080/"],
        public_base_url="https://crew.example.trycloudflare.com/some/path",
    )
    assert settings.allowed_origins() == {
        "http://localhost:8080",
        "https://crew.example.trycloudflare.com",
    }
