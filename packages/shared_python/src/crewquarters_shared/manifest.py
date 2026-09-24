"""Agent manifest validation and model-profile resolution.

The canonical schema is ``packages/contracts/agent-manifest.schema.json``.
Profile resolution follows PLAN.md section 8.1: a manifest may request a profile
*family* (``local.general``) or an exact *variant* (``local.general.small``).
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from crewquarters_shared.config import get_settings
from crewquarters_shared.errors import invalid
from crewquarters_shared.schema_guard import check_schema

# Default variant chosen when a manifest requests only a family.
DEFAULT_VARIANTS: dict[str, str] = {
    "local.general": "local.general.small",
    "local.embedding": "local.embedding.small",
}
# Variants known to the v1 catalog. The model gateway owner extends this list.
KNOWN_VARIANTS: frozenset[str] = frozenset(
    {"local.general.small", "local.general.quality", "local.embedding.small"}
)

MAX_INPUT_WAIT_SECONDS = 86_400


@lru_cache
def _validator(contracts_dir: Path) -> Draft202012Validator:
    schema = json.loads((contracts_dir / "agent-manifest.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def manifest_validator() -> Draft202012Validator:
    return _validator(get_settings().contracts_dir)


def _errors(validator: Draft202012Validator, instance: Any) -> list[dict[str, str]]:
    return [
        {"path": "/" + "/".join(str(p) for p in err.absolute_path), "message": err.message}
        for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    ][:20]


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a manifest. Raises ``PlatformError`` (422) on failure."""
    errors = _errors(manifest_validator(), manifest)
    if errors:
        raise invalid("INVALID_MANIFEST", "The agent manifest is invalid.", errors=errors)
    normalized = copy.deepcopy(manifest)
    spec = normalized["spec"]
    spec.setdefault("sdkProtocol", "v1alpha1")
    spec["resources"].setdefault("maxInputWaitSeconds", MAX_INPUT_WAIT_SECONDS)
    spec["resources"].setdefault("pids", 256)
    spec["permissions"]["connectors"].setdefault("google", [])
    spec["permissions"]["connectors"].setdefault("twilio", [])
    config_schema = spec.setdefault(
        "configurationSchema", {"type": "object", "properties": {}, "additionalProperties": False}
    )
    check_schema(config_schema, code="INVALID_MANIFEST", allow_patterns=True)
    if spec.get("resultSchema") is not None:
        check_schema(spec["resultSchema"], code="INVALID_MANIFEST", allow_patterns=True)
    for profile in spec["permissions"]["llmProfiles"]:
        is_known = profile in DEFAULT_VARIANTS or profile in KNOWN_VARIANTS
        if profile.startswith("local.") and not is_known:
            raise invalid("UNKNOWN_MODEL_PROFILE", f"Unknown model profile {profile}.")
    return normalized


def image_digest(image: str) -> str:
    return image.rsplit("@", 1)[1]


def profile_family(profile: str) -> str:
    return ".".join(profile.split(".")[:2])


def resolve_model_bindings(
    requested_profiles: list[str], owner_choices: dict[str, str] | None = None
) -> dict[str, str]:
    """Map each requested profile to the exact variant the installation may use."""
    choices = owner_choices or {}
    unknown = set(choices) - set(requested_profiles)
    if unknown:
        raise invalid(
            "INVALID_MODEL_BINDING",
            "Model choices must refer to profiles the agent requests.",
            profiles=sorted(unknown),
        )
    bindings: dict[str, str] = {}
    for profile in requested_profiles:
        if profile in DEFAULT_VARIANTS:  # family request
            chosen = choices.get(profile, DEFAULT_VARIANTS[profile])
            if profile_family(chosen) != profile or chosen not in KNOWN_VARIANTS:
                raise invalid(
                    "INVALID_MODEL_BINDING",
                    f"{chosen} is not a variant of {profile}.",
                    profile=profile,
                    choice=chosen,
                )
            bindings[profile] = chosen
        else:  # exact variant or cloud profile: no choice allowed
            if profile in choices and choices[profile] != profile:
                raise invalid(
                    "INVALID_MODEL_BINDING",
                    f"{profile} is an exact profile and cannot be changed.",
                    profile=profile,
                )
            bindings[profile] = profile
    return bindings


def apply_config_defaults(schema: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config)
    for name, prop in (schema.get("properties") or {}).items():
        if name not in out and isinstance(prop, dict) and "default" in prop:
            out[name] = copy.deepcopy(prop["default"])
    return out


def validate_config(
    manifest: dict[str, Any], config: dict[str, Any], bindings: dict[str, str]
) -> dict[str, Any]:
    schema = manifest["spec"]["configurationSchema"]
    filled = apply_config_defaults(schema, config)
    errors = _errors(Draft202012Validator(schema), filled)
    if errors:
        raise invalid("INVALID_CONFIGURATION", "The agent configuration is invalid.", errors=errors)
    model_profile = filled.get("modelProfile")
    if model_profile is not None and model_profile not in set(bindings.values()):
        raise invalid(
            "MODEL_PROFILE_NOT_PERMITTED",
            "modelProfile must be one of the approved model profiles.",
            modelProfile=model_profile,
            permitted=sorted(set(bindings.values())),
        )
    return filled


def load_manifest_file(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise invalid("INVALID_MANIFEST", f"{path.name} does not contain a manifest object.")
    return data
