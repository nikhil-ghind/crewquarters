"""Speech model profiles and the SIP conversational-call permission (voice call center agent)."""

from __future__ import annotations

import json

import pytest
import yaml
from jsonschema import Draft202012Validator

from conftest import ROOT
from crewquarters_shared import capability, manifest

pytestmark = pytest.mark.no_db
CONTRACTS = ROOT / "packages/contracts"


def voice_permissions() -> dict[str, object]:
    return {
        "llmProfiles": ["local.general", "local.stt", "local.tts"],
        "knowledge": [],
        "connectors": {"google": ["spreadsheets"], "sip": ["call.conversational"]},
        "cloudProviders": [],
        "userInput": True,
    }


def test_speech_families_resolve_to_default_variants() -> None:
    bindings = manifest.resolve_model_bindings(["local.stt", "local.tts", "local.general"])
    assert bindings == {
        "local.stt": "local.stt.small",
        "local.tts": "local.tts.small",
        "local.general": "local.general.small",
    }


def test_manifest_requesting_speech_variants_validates() -> None:
    hello = yaml.safe_load((ROOT / "catalog/dev/hello-crew.yaml").read_text())
    hello["spec"]["permissions"] = voice_permissions()
    hello["spec"]["permissions"]["llmProfiles"] = ["local.general", "local.stt.small", "local.tts"]
    normalized = manifest.validate_manifest(hello)
    assert normalized["spec"]["permissions"]["connectors"]["sip"] == ["call.conversational"]


def test_sip_connector_grants_conversational_calls() -> None:
    bindings = manifest.resolve_model_bindings(["local.general", "local.stt", "local.tts"])
    caps = capability.capabilities_from_permissions(voice_permissions(), bindings)
    assert {
        "sip.call.conversational",
        "llm.profile:local.stt.small",
        "llm.profile:local.tts.small",
        "google.spreadsheets",
    } <= set(caps)


def test_voice_permissions_validate_against_the_manifest_schema() -> None:
    schema = json.loads((CONTRACTS / "agent-manifest.schema.json").read_text())
    permissions_schema = {**schema["$defs"]["permissions"], "$defs": schema["$defs"]}
    assert list(Draft202012Validator(permissions_schema).iter_errors(voice_permissions())) == []
    bad = voice_permissions()
    bad["connectors"] = {"sip": ["call.anything"]}
    assert list(Draft202012Validator(permissions_schema).iter_errors(bad))


def test_sip_capability_is_in_the_vocabulary() -> None:
    vocab = yaml.safe_load((CONTRACTS / "capabilities.yaml").read_text())
    [entry] = [c for c in vocab["capabilities"] if c["pattern"] == "sip.call.conversational"]
    assert entry["from"] == "permissions.connectors.sip"
    assert entry["enforcedBy"] == "capability_broker"


def test_manifests_without_sip_normalize_unchanged() -> None:
    hello = yaml.safe_load((ROOT / "catalog/dev/hello-crew.yaml").read_text())
    normalized = manifest.validate_manifest(hello)
    assert "sip" not in normalized["spec"]["permissions"]["connectors"]
