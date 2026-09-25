"""The fake platform serves a subset of the control API's operations (same paths and methods)
and exactly the operations the broker SDK draft describes."""

import re
from typing import Any

from crewquarters_fake import contracts
from crewquarters_fake.app import create_app
from crewquarters_fake.settings import FakeSettings

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def template(path: str) -> str:
    """Path parameter names do not change the wire contract, so compare templates only."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def contract_operations(document: dict[str, Any], prefix: str) -> set[tuple[str, str]]:
    return {
        (method.upper(), template(prefix + path))
        for path, item in document["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def served_operations(prefix: str) -> set[tuple[str, str]]:
    paths = create_app(FakeSettings()).openapi()["paths"]
    return {
        (method.upper(), template(path))
        for path, item in paths.items()
        if path.startswith(prefix)
        for method in item
        if method in HTTP_METHODS
    }


def test_broker_routes_match_the_broker_draft() -> None:
    expected = contract_operations(contracts.broker_openapi(), "/internal/v1/sdk")
    assert served_operations("/internal/v1/sdk") == expected


def test_control_routes_are_canonical_control_api_operations() -> None:
    canonical = contract_operations(contracts.control_openapi(), "")
    served = served_operations("/api/v1")
    assert served <= canonical, served - canonical
    # The operations agent development and the demo depend on.
    assert {
        ("POST", "/api/v1/catalog/agents/import"),
        ("POST", "/api/v1/agent-installations"),
        ("POST", "/api/v1/runs"),
        ("GET", "/api/v1/runs/{}"),
        ("POST", "/api/v1/runs/{}/cancel"),
        ("POST", "/api/v1/runs/{}/retry"),
        ("GET", "/api/v1/runs/{}/events/history"),
        ("GET", "/api/v1/input-requests"),
        ("POST", "/api/v1/input-requests/{}/answer"),
    } <= served
