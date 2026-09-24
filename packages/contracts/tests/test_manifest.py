import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from crewquarters_contracts.manifest import (
    Issue,
    apply_config_defaults,
    config_issues,
    derive_capabilities,
    image_digest,
    load_manifest,
    validate_manifest,
    widget_values,
)

DIGEST = "sha256:" + "a" * 64


def valid_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "crewquarters/v1alpha1",
        "kind": "Agent",
        "metadata": {"id": "daily-gmail-digest", "name": "Daily Gmail Digest", "version": "0.1.0"},
        "spec": {
            "image": f"localhost:5001/crewquarters/daily-gmail-digest@{DIGEST}",
            "entrypoint": ["python", "-m", "gmail_digest"],
            "architectures": ["linux/amd64", "linux/arm64"],
            "triggers": ["manual", "schedule"],
            "permissions": {
                "llmProfiles": ["local.general"],
                "knowledge": [],
                "connectors": {"google": ["gmail.readonly"]},
                "cloudProviders": [],
                "userInput": False,
            },
            "resources": {
                "cpu": 1,
                "memoryMb": 512,
                "activeTimeoutSeconds": 1800,
                "maxInputWaitSeconds": 0,
            },
            "configurationSchema": {
                "type": "object",
                "required": ["timezone"],
                "properties": {
                    "timezone": {"type": "string", "x-crewquarters-widget": "timezone"},
                    "maxMessages": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
                },
            },
            "result": {"renderer": "crewquarters.gmail-digest/v1", "schema": {"type": "object"}},
        },
    }


def messages(issues: list[Issue]) -> str:
    return "\n".join(f"{i.path}: {i.message}" for i in issues)


def test_valid_manifest_has_no_issues() -> None:
    assert validate_manifest(valid_manifest()) == []


def test_unknown_key_is_rejected() -> None:
    m = valid_manifest()
    m["spec"]["privileged"] = True
    issues = validate_manifest(m)
    assert any("privileged" in i.message for i in issues), messages(issues)


def test_unknown_resource_key_is_rejected() -> None:
    m = valid_manifest()
    m["spec"]["resources"]["mounts"] = ["/"]
    assert validate_manifest(m)


def test_required_digest_placeholder_needs_allow_unbuilt() -> None:
    m = valid_manifest()
    m["spec"]["image"] = "ghcr.io/example/agent@sha256:REQUIRED_DIGEST"
    issues = validate_manifest(m)
    assert [i.path for i in issues] == ["spec.image"], messages(issues)
    assert validate_manifest(m, allow_unbuilt=True) == []


def test_mutable_tag_is_rejected_even_when_unbuilt_allowed() -> None:
    m = valid_manifest()
    m["spec"]["image"] = "ghcr.io/example/agent:latest"
    assert validate_manifest(m, allow_unbuilt=True)


def test_cloud_profile_requires_matching_provider() -> None:
    m = valid_manifest()
    m["spec"]["permissions"]["llmProfiles"] = ["cloud.openai.gpt-small"]
    issues = validate_manifest(m)
    assert any("cloudProviders" in i.message for i in issues), messages(issues)


def test_cloud_provider_requires_a_profile() -> None:
    m = valid_manifest()
    m["spec"]["permissions"]["cloudProviders"] = ["anthropic"]
    issues = validate_manifest(m)
    assert any("anthropic" in i.message for i in issues), messages(issues)


def test_input_wait_must_be_zero_without_user_input() -> None:
    m = valid_manifest()
    m["spec"]["resources"]["maxInputWaitSeconds"] = 60
    issues = validate_manifest(m)
    assert any(i.path == "spec.resources.maxInputWaitSeconds" for i in issues), messages(issues)


def test_input_wait_above_platform_cap_is_rejected() -> None:
    m = valid_manifest()
    m["spec"]["permissions"]["userInput"] = True
    m["spec"]["resources"]["maxInputWaitSeconds"] = 86_401
    assert validate_manifest(m)


def test_invalid_configuration_schema_is_rejected() -> None:
    m = valid_manifest()
    m["spec"]["configurationSchema"]["properties"]["maxMessages"]["type"] = "whole-number"
    issues = validate_manifest(m)
    assert any(i.path.startswith("spec.configurationSchema") for i in issues), messages(issues)


def test_configuration_schema_root_must_be_object() -> None:
    m = valid_manifest()
    m["spec"]["configurationSchema"] = {"type": "string"}
    assert validate_manifest(m)


def test_default_that_violates_its_schema_is_rejected() -> None:
    m = valid_manifest()
    m["spec"]["configurationSchema"]["properties"]["maxMessages"]["default"] = 9999
    issues = validate_manifest(m)
    assert any("maxMessages" in i.path for i in issues), messages(issues)


def test_invalid_result_schema_is_rejected() -> None:
    m = valid_manifest()
    m["spec"]["result"]["schema"] = {"type": 12}
    assert validate_manifest(m)


def test_bad_profile_name_is_rejected() -> None:
    m = valid_manifest()
    m["spec"]["permissions"]["llmProfiles"] = ["gpt-4"]
    assert validate_manifest(m)


@pytest.mark.parametrize(
    ("permissions", "expected"),
    [
        ({"userInput": True}, {"input.ask"}),
        ({"llmProfiles": ["local.general"]}, {"llm.local"}),
        ({"llmProfiles": ["local.general.quality"]}, {"llm.local"}),
        (
            {"llmProfiles": ["cloud.openai.gpt-small"], "cloudProviders": ["openai"]},
            {"llm.cloud.openai"},
        ),
        (
            {"llmProfiles": ["cloud.anthropic.claude-small"], "cloudProviders": ["anthropic"]},
            {"llm.cloud.anthropic"},
        ),
        ({"knowledge": ["search"]}, {"knowledge.search"}),
        ({"connectors": {"google": ["gmail.readonly"]}}, {"google.gmail.readonly"}),
        ({"connectors": {"google": ["spreadsheets"]}}, {"google.sheets"}),
        ({"connectors": {"twilio": ["voice.call"]}}, {"twilio.voice.call"}),
        (
            {"llmProfiles": ["cloud.openai.gpt-small"], "cloudProviders": []},
            set(),
        ),
        ({"userInput": False, "knowledge": []}, set()),
    ],
)
def test_derive_capabilities(permissions: dict[str, Any], expected: set[str]) -> None:
    assert derive_capabilities(permissions) == frozenset(expected)


def test_image_digest() -> None:
    assert image_digest(f"repo/x@{DIGEST}") == DIGEST
    assert image_digest("repo/x@sha256:REQUIRED_DIGEST") is None
    assert image_digest("repo/x:1.0") is None


def test_apply_config_defaults_fills_top_level_defaults_only() -> None:
    schema = valid_manifest()["spec"]["configurationSchema"]
    assert apply_config_defaults(schema, {"timezone": "UTC"}) == {"timezone": "UTC", "maxMessages": 200}
    assert apply_config_defaults(schema, {"timezone": "UTC", "maxMessages": 5})["maxMessages"] == 5


def test_apply_config_defaults_does_not_mutate_input() -> None:
    schema = valid_manifest()["spec"]["configurationSchema"]
    config = {"timezone": "UTC"}
    before = copy.deepcopy(config)
    apply_config_defaults(schema, config)
    assert config == before


def test_config_issues() -> None:
    schema = valid_manifest()["spec"]["configurationSchema"]
    assert config_issues(schema, {"timezone": "UTC", "maxMessages": 10}) == []
    issues = config_issues(schema, {"maxMessages": 0})
    assert {i.path for i in issues} == {"config", "config.maxMessages"}, messages(issues)


def test_widget_values() -> None:
    schema = {
        "type": "object",
        "properties": {
            "kb": {"type": "string", "x-crewquarters-widget": "knowledgeBase"},
            "other": {"type": "string"},
        },
    }
    assert widget_values(schema, {"kb": "kb-1", "other": "x"}, "knowledgeBase") == ["kb-1"]
    assert widget_values(schema, {}, "knowledgeBase") == []


def test_load_manifest_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(valid_manifest()))
    assert load_manifest(path) == valid_manifest()


def test_load_manifest_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_manifest(path)
