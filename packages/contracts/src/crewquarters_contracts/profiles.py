"""Model profile family/variant resolution (PLAN.md section 8.1).

A profile name has a family (``local.general``) and an optional variant (``.small``). A manifest may
request a family or an exact variant. Cloud profiles (``cloud.<provider>.<name>``) always resolve to
themselves.
"""

from __future__ import annotations

FAMILIES = frozenset({"local.general", "local.embedding"})
FAMILY_DEFAULT_VARIANT = "small"


def is_family(name: str) -> bool:
    return name in FAMILIES


def family_of(name: str) -> str:
    if name.startswith("local."):
        return ".".join(name.split(".")[:2])
    return name


def resolve_profiles(approved: list[str], model_profile: str | None) -> list[str]:
    """Resolve approved manifest profiles to the exact variants a run token carries.

    ``model_profile`` (the installation's configured choice) must be permitted by an approved entry;
    otherwise ``ValueError`` is raised.
    """
    resolved: list[str] = []
    for profile in approved:
        if is_family(profile):
            if (
                model_profile is not None
                and family_of(model_profile) == profile
                and not is_family(model_profile)
            ):
                variant = model_profile
            else:
                variant = f"{profile}.{FAMILY_DEFAULT_VARIANT}"
        else:
            variant = profile
        if variant not in resolved:
            resolved.append(variant)
    if model_profile is not None and model_profile not in resolved:
        raise ValueError(f"modelProfile {model_profile} is not permitted by the approved profiles {approved}")
    return resolved
