"""`crewctl publish --target local`: import a digest-pinned manifest into the local catalog.

The control API only accepts writes from a signed-in owner: pass a username and password to sign
in (the session cookie, CSRF token, and Origin header are handled here). The fake platform needs
no credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from crewctl.validate import validate_path


class PublishError(Exception):
    pass


def _error(response: httpx.Response) -> PublishError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error", {}) if isinstance(body, dict) else {}
    code = error.get("code", response.status_code)
    return PublishError(f"{code}: {error.get('message', response.text)}")


def publish(
    path: Path,
    platform_url: str,
    *,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    manifest, issues = validate_path(path, allow_unbuilt=False)
    if issues or manifest is None:
        raise PublishError("; ".join(f"{i.path}: {i.message}" for i in issues))
    base = platform_url.rstrip("/")
    headers = {"Origin": base}
    try:
        with httpx.Client(base_url=base, timeout=30, headers=headers) as http:
            if username is not None:
                login = http.post(
                    "/api/v1/sessions", json={"username": username, "password": password or ""}
                )
                if login.status_code >= 400:
                    raise _error(login)
                http.headers["X-CSRF-Token"] = str(login.json()["csrfToken"])
            response = http.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    except httpx.HTTPError as exc:
        raise PublishError(f"could not reach {platform_url}: {exc}") from exc
    if response.status_code >= 400:
        raise _error(response)
    return dict(response.json())
