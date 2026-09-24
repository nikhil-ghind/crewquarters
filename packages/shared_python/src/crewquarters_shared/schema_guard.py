"""Guards for JSON Schemas and payloads supplied by agents or imported manifests.

``jsonschema`` evaluates ``pattern`` with Python's backtracking ``re``; a crafted
pattern such as ``^(a+)+$`` can block the event loop for minutes. Agent-supplied
input schemas therefore may not use regular expressions at all, and manifest
configuration schemas may use only short patterns without repeated groups that
contain quantifiers or alternations.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from crewquarters_shared.errors import invalid

MAX_SCHEMA_BYTES = 16 * 1024
MAX_PATTERN_LENGTH = 200
_REGEX_KEYWORDS = frozenset({"pattern", "patternProperties"})
# A repeated group that contains a quantifier or an alternation: (a+)+, (a|aa)+, (x*){2,}.
# This conservative heuristic rejects the classic exponential-backtracking shapes; it is
# not a proof of safety, which is why agent-supplied input schemas allow no regex at all.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[*+}|](?:[^()\\]|\\.)*\)\s*[*+{]")


def json_size(value: Any) -> int:
    return len(json.dumps(value, default=str, separators=(",", ":")))


def check_size(value: Any, limit: int, code: str, what: str) -> None:
    if json_size(value) > limit:
        raise invalid(code, f"{what} is larger than {limit // 1024} KiB.")


def _patterns(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                found.append(value)
            elif key == "patternProperties" and isinstance(value, dict):
                found.extend(str(k) for k in value)
            found.extend(_patterns(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_patterns(item))
    return found


def _has_regex_keyword(node: Any) -> bool:
    if isinstance(node, dict):
        return bool(_REGEX_KEYWORDS & node.keys()) or any(
            _has_regex_keyword(v) for v in node.values()
        )
    if isinstance(node, list):
        return any(_has_regex_keyword(v) for v in node)
    return False


def check_schema(schema: dict[str, Any], *, code: str, allow_patterns: bool) -> None:
    """Validate a JSON Schema and reject shapes that make validation unsafe."""
    check_size(schema, MAX_SCHEMA_BYTES, code, "The schema")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise invalid(code, "The schema is not a valid JSON Schema.", reason=exc.message) from exc
    if not allow_patterns:
        if _has_regex_keyword(schema):
            raise invalid(code, "Input schemas may not use pattern or patternProperties.")
        return
    for pattern in _patterns(schema):
        if len(pattern) > MAX_PATTERN_LENGTH or _NESTED_QUANTIFIER.search(pattern):
            raise invalid(
                code,
                "Schema patterns must be short and must not nest quantifiers.",
                pattern=pattern[:80],
            )
