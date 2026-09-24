"""Agent manifest validation shared by crewctl and the platform (fake or real)."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from crewquarters_contracts.loader import manifest_schema

_DIGEST_RE = re.compile(r"@(sha256:[a-f0-9]{64})$")
UNBUILT_MARKER = "@sha256:REQUIRED_DIGEST"


@dataclass(frozen=True)
class Issue:
    path: str
    message: str


@cache
def _manifest_validator() -> Draft202012Validator:
    return Draft202012Validator(manifest_schema())


def _join(prefix: str, parts: Any) -> str:
    tail = ".".join(str(p) for p in parts)
    if not tail:
        return prefix
    return f"{prefix}.{tail}" if prefix else tail


def load_manifest(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: a manifest must be a YAML mapping")
    return document


def schema_issues(manifest: dict[str, Any]) -> list[Issue]:
    errors = sorted(_manifest_validator().iter_errors(manifest), key=lambda e: list(e.absolute_path))
    return [Issue(_join("", e.absolute_path), e.message) for e in errors]


def _schema_document_issues(path: str, schema: Any) -> list[Issue]:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [Issue(_join(path, exc.absolute_path), f"invalid JSON Schema: {exc.message}")]
    return []


def semantic_issues(manifest: dict[str, Any], *, allow_unbuilt: bool) -> list[Issue]:
    """Rules the JSON Schema cannot express. Assumes ``schema_issues(manifest)`` is empty."""
    spec = manifest["spec"]
    issues: list[Issue] = []

    if spec["image"].endswith(UNBUILT_MARKER) and not allow_unbuilt:
        issues.append(
            Issue(
                "spec.image",
                "image is not pinned by digest; run `crewctl build --push` (or pass --allow-unbuilt)",
            )
        )

    config_schema = spec["configurationSchema"]
    doc_issues = _schema_document_issues("spec.configurationSchema", config_schema)
    issues.extend(doc_issues)
    if not doc_issues:
        if config_schema.get("type") != "object":
            issues.append(
                Issue("spec.configurationSchema.type", "the configuration schema root must be type object")
            )
        for name, prop in config_schema.get("properties", {}).items():
            if isinstance(prop, dict) and "default" in prop:
                for error in Draft202012Validator(prop).iter_errors(prop["default"]):
                    issues.append(
                        Issue(
                            f"spec.configurationSchema.properties.{name}.default",
                            f"default is invalid: {error.message}",
                        )
                    )

    if "result" in spec:
        issues.extend(_schema_document_issues("spec.result.schema", spec["result"]["schema"]))

    permissions = spec["permissions"]
    providers = set(permissions.get("cloudProviders", []))
    requested_providers = set()
    for profile in permissions.get("llmProfiles", []):
        if profile.startswith("cloud."):
            provider = profile.split(".")[1]
            requested_providers.add(provider)
            if provider not in providers:
                issues.append(
                    Issue(
                        "spec.permissions.llmProfiles",
                        f"{profile} requires cloudProviders to include {provider}",
                    )
                )
    for provider in sorted(providers - requested_providers):
        issues.append(
            Issue(
                "spec.permissions.cloudProviders",
                f"{provider} is declared but no cloud.{provider}.* profile in llmProfiles uses it",
            )
        )

    if not permissions.get("userInput", False) and spec["resources"]["maxInputWaitSeconds"] > 0:
        issues.append(
            Issue("spec.resources.maxInputWaitSeconds", "must be 0 when permissions.userInput is false")
        )
    return issues


def validate_manifest(manifest: dict[str, Any], *, allow_unbuilt: bool = False) -> list[Issue]:
    issues = schema_issues(manifest)
    if issues:
        return issues
    return semantic_issues(manifest, allow_unbuilt=allow_unbuilt)


def derive_capabilities(permissions: dict[str, Any]) -> frozenset[str]:
    """Map manifest (or approved) permissions to capability strings (capabilities.yaml)."""
    capabilities: set[str] = set()
    if permissions.get("userInput", False):
        capabilities.add("input.ask")
    providers = set(permissions.get("cloudProviders", []))
    for profile in permissions.get("llmProfiles", []):
        if profile.startswith("local."):
            capabilities.add("llm.local")
        elif profile.startswith("cloud."):
            provider = profile.split(".")[1]
            if provider in providers:
                capabilities.add(f"llm.cloud.{provider}")
    if "search" in permissions.get("knowledge", []):
        capabilities.add("knowledge.search")
    connectors = permissions.get("connectors", {})
    google = connectors.get("google", [])
    if "gmail.readonly" in google:
        capabilities.add("google.gmail.readonly")
    if "spreadsheets" in google:
        capabilities.add("google.sheets")
    if "voice.call" in connectors.get("twilio", []):
        capabilities.add("twilio.voice.call")
    return frozenset(capabilities)


def image_digest(image: str) -> str | None:
    match = _DIGEST_RE.search(image)
    return match.group(1) if match else None


def apply_config_defaults(schema: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``config`` with top-level property defaults filled in."""
    filled = copy.deepcopy(config)
    for name, prop in schema.get("properties", {}).items():
        if isinstance(prop, dict) and "default" in prop and name not in filled:
            filled[name] = copy.deepcopy(prop["default"])
    return filled


def config_issues(schema: dict[str, Any], config: dict[str, Any]) -> list[Issue]:
    errors = sorted(Draft202012Validator(schema).iter_errors(config), key=lambda e: list(e.absolute_path))
    return [Issue(_join("config", e.absolute_path), e.message) for e in errors]


def widget_values(schema: dict[str, Any], config: dict[str, Any], widget: str) -> list[str]:
    """Configured string values of the properties that carry ``x-crewquarters-widget: <widget>``."""
    values: list[str] = []
    for name, prop in schema.get("properties", {}).items():
        value = config.get(name)
        if (
            isinstance(prop, dict)
            and prop.get("x-crewquarters-widget") == widget
            and isinstance(value, str)
            and value
        ):
            values.append(value)
    return values
