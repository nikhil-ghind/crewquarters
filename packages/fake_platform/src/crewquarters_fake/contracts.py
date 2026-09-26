"""Contract helpers for the dev tools, built on the control plane's shared library.

Manifest validation, model-profile bindings, configuration checks, and capability derivation all
come from ``crewquarters_shared`` (Person 1's canonical implementation), so the fake platform and
crewctl accept exactly what the real control API accepts. The only addition is the dev-tool
convention for unbuilt manifests: an image ending in ``@sha256:REQUIRED_DIGEST`` is validated with
a placeholder digest and reported as unbuilt.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from crewquarters_shared import capability
from crewquarters_shared import manifest as shared_manifest
from crewquarters_shared.config import get_settings
from crewquarters_shared.errors import PlatformError

UNBUILT_MARKER = "@sha256:REQUIRED_DIGEST"
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


@dataclass(frozen=True)
class Issue:
    path: str
    message: str


def contracts_dir() -> Path:
    return get_settings().contracts_dir


def _load(relative: str) -> dict[str, Any]:
    path = contracts_dir() / relative
    text = path.read_text(encoding="utf-8")
    document = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError(f"{path} does not contain a mapping")
    return document


def manifest_schema() -> dict[str, Any]:
    return _load("agent-manifest.schema.json")


def run_event_schema() -> dict[str, Any]:
    return _load("events/run-event.schema.json")


def control_openapi() -> dict[str, Any]:
    return _load("openapi.yaml")


def broker_openapi() -> dict[str, Any]:
    """The broker contract the fake serves: the stable file plus the voice draft (D24), whose
    paths and schemas are additions only."""
    document = _load("broker-sdk.openapi.yaml")
    draft = _load("broker-sdk.voice.openapi.yaml")
    document["paths"] = {**document["paths"], **draft["paths"]}
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(draft["components"]["schemas"])
    return document


def load_manifest(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: a manifest must be a YAML mapping")
    return document


def is_unbuilt(manifest: dict[str, Any]) -> bool:
    image = manifest.get("spec", {}).get("image", "")
    return isinstance(image, str) and image.endswith(UNBUILT_MARKER)


def _with_placeholder_digest(manifest: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(manifest)
    image = candidate["spec"]["image"]
    candidate["spec"]["image"] = image[: -len(UNBUILT_MARKER)] + "@" + PLACEHOLDER_DIGEST
    return candidate


def _issues(error: PlatformError) -> list[Issue]:
    details = error.details.get("errors")
    if isinstance(details, list) and details:
        return [Issue(str(e.get("path", "")), str(e.get("message", ""))) for e in details]
    return [Issue(error.code, error.message)]


def validate_manifest(
    manifest: dict[str, Any], *, allow_unbuilt: bool = False
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """Validate with the control plane's rules. Returns (normalized manifest, issues)."""
    unbuilt = is_unbuilt(manifest)
    if unbuilt and not allow_unbuilt:
        return None, [
            Issue(
                "/spec/image",
                "image is not pinned by digest; "
                "run `crewctl build --push` (or pass --allow-unbuilt)",
            )
        ]
    candidate = _with_placeholder_digest(manifest) if unbuilt else manifest
    try:
        normalized = shared_manifest.validate_manifest(candidate)
    except PlatformError as exc:
        return None, _issues(exc)
    if unbuilt:
        normalized["spec"]["image"] = manifest["spec"]["image"]
    return normalized, []


def model_bindings(requested: list[str], choices: dict[str, str] | None = None) -> dict[str, str]:
    """Owner choice of variant per requested family (raises PlatformError 422 on bad choices)."""
    return shared_manifest.resolve_model_bindings(requested, choices)


def validate_config(
    manifest: dict[str, Any], config: dict[str, Any], bindings: dict[str, str]
) -> dict[str, Any]:
    """Apply defaults and validate (raises PlatformError 422)."""
    return shared_manifest.validate_config(manifest, config, bindings)


def capabilities(permissions: dict[str, Any], bindings: dict[str, str]) -> list[str]:
    return capability.capabilities_from_permissions(permissions, bindings)


def image_digest(image: str) -> str | None:
    if image.endswith(UNBUILT_MARKER) or "@sha256:" not in image:
        return None
    return image.rsplit("@", 1)[1]
