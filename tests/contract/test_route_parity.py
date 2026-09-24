"""The fake platform serves exactly the operations the draft OpenAPI documents describe."""

import re

from crewquarters_contracts import loader
from crewquarters_fake.app import create_app
from crewquarters_fake.settings import FakeSettings

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def template(path: str) -> str:
    """Path parameter names do not change the wire contract, so compare templates only."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def contract_operations(document: dict, prefix: str) -> set[tuple[str, str]]:  # type: ignore[type-arg]
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


def test_broker_routes_match_broker_contract() -> None:
    expected = contract_operations(loader.broker_openapi(), "/internal/v1/sdk")
    assert served_operations("/internal/v1/sdk") == expected


def test_control_routes_match_control_contract() -> None:
    expected = contract_operations(loader.control_openapi(), "")
    assert served_operations("/api/v1") == expected
