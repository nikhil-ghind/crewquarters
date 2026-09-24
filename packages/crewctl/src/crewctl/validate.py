"""`crewctl validate`: manifest schema, semantic rules, and local entrypoint checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crewquarters_contracts.manifest import Issue, derive_capabilities, load_manifest, validate_manifest


def manifest_path(path: Path) -> Path:
    return path / "manifest.yaml" if path.is_dir() else path


def entrypoint_issues(manifest: dict[str, Any], agent_dir: Path) -> list[Issue]:
    entrypoint = manifest["spec"]["entrypoint"]
    src = agent_dir / "src"
    if len(entrypoint) < 3 or entrypoint[:2] != ["python", "-m"] or not src.is_dir():
        return []
    module = entrypoint[2]
    if (src / module / "__main__.py").is_file() or (src / f"{module}.py").is_file():
        return []
    return [Issue("spec.entrypoint", f"module {module} has no src/{module}/__main__.py")]


def validate_path(path: Path, *, allow_unbuilt: bool) -> tuple[dict[str, Any] | None, list[Issue]]:
    target = manifest_path(path)
    if not target.is_file():
        return None, [Issue("manifest.yaml", f"no manifest found at {target}")]
    try:
        manifest = load_manifest(target)
    except (ValueError, OSError) as exc:
        return None, [Issue("manifest.yaml", str(exc))]
    issues = validate_manifest(manifest, allow_unbuilt=allow_unbuilt)
    if not issues:
        issues = entrypoint_issues(manifest, target.parent)
    return manifest, issues


def capabilities_of(manifest: dict[str, Any]) -> list[str]:
    return sorted(derive_capabilities(manifest["spec"]["permissions"]))
