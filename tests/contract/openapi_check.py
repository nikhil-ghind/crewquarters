"""Validate recorded HTTP traffic against OpenAPI documents (formats included)."""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    pattern = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{pattern}$")


def find_operation(
    document: dict[str, Any], prefix: str, method: str, path: str
) -> dict[str, Any] | None:
    for template, item in document["paths"].items():
        if _template_regex(prefix + template).match(path):
            operation = item.get(method.lower())
            if operation is not None:
                return dict(operation)
    return None


def validator(document: dict[str, Any], schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(
        {**schema, "components": document["components"]},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _json_schema(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    content = (entry or {}).get("content", {}).get("application/json")
    return content.get("schema") if content else None


def request_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    return _json_schema(operation.get("requestBody"))


def response_schema(
    document: dict[str, Any], operation: dict[str, Any], status: int
) -> dict[str, Any] | None:
    responses = operation["responses"]
    entry = responses.get(str(status)) or responses.get("default")
    if entry and "$ref" in entry:
        entry = document["components"]["responses"][entry["$ref"].rsplit("/", 1)[-1]]
    return _json_schema(entry)
