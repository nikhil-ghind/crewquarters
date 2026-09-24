"""Deterministic OpenAPI rendering for ``packages/contracts/openapi.yaml``.

The committed file is the canonical HTTP contract. CI regenerates it and fails on
any difference, so behavior and documentation cannot drift apart.
"""

from __future__ import annotations

from typing import Any

import yaml

from crewquarters_shared.config import Settings


def build_openapi() -> dict[str, Any]:
    from crewquarters_api.main import create_app

    app = create_app(Settings(catalog_dir=None))
    spec = app.openapi()
    spec["servers"] = [{"url": "/", "description": "Same origin as the web UI"}]
    spec.setdefault("components", {})["securitySchemes"] = {
        "session": {"type": "apiKey", "in": "cookie", "name": "cq_session"},
        "csrf": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"},
        "service": {
            "type": "http",
            "scheme": "bearer",
            "description": "Internal service credential",
        },
    }
    return spec


def render_openapi() -> str:
    header = (
        "# GENERATED from services/control_api by `make contracts`. Do not edit by hand.\n"
        "# Canonical HTTP contract for the Crewquarters control API (packages/contracts).\n"
    )
    return header + yaml.safe_dump(build_openapi(), sort_keys=False, allow_unicode=True, width=100)
