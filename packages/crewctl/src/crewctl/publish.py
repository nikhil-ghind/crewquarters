"""`crewctl publish --target local`: import a digest-pinned manifest into the local catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from crewctl.validate import validate_path


class PublishError(Exception):
    pass


def publish(path: Path, platform_url: str) -> dict[str, Any]:
    manifest, issues = validate_path(path, allow_unbuilt=False)
    if issues or manifest is None:
        raise PublishError("; ".join(f"{i.path}: {i.message}" for i in issues))
    try:
        response = httpx.post(
            f"{platform_url.rstrip('/')}/api/v1/catalog/agents:import",
            json={"manifest": manifest},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise PublishError(f"could not reach {platform_url}: {exc}") from exc
    body = response.json() if response.content else {}
    if response.status_code >= 400:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        raise PublishError(
            f"{error.get('code', response.status_code)}: {error.get('message', response.text)}"
        )
    return dict(body)
