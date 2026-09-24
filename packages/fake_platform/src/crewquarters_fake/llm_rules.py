"""Rule-driven mock LLM responses (spec section 6.4)."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from crewquarters.untrusted import parse_evidence


@dataclass
class Rule:
    name: str
    schema_title: str | None = None
    contains: list[str] = field(default_factory=list)
    regex: str | None = None
    respond: dict[str, Any] = field(default_factory=dict)
    delay_ms: int = 0

    def matches(self, text: str, response_schema: dict[str, Any] | None) -> bool:
        if self.schema_title is not None and (response_schema or {}).get("title") != self.schema_title:
            return False
        lowered = text.lower()
        if any(needle.lower() not in lowered for needle in self.contains):
            return False
        return not (self.regex is not None and re.search(self.regex, text, re.IGNORECASE) is None)


def _substitute(value: Any, ref: str) -> Any:
    if isinstance(value, str):
        return value.replace("{ref}", ref)
    if isinstance(value, dict):
        return {k: _substitute(v, ref) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, ref) for v in value]
    return value


def _per_evidence(spec: dict[str, Any], text: str) -> dict[str, Any]:
    items = []
    for block in parse_evidence(text):
        item = _substitute(copy.deepcopy(spec["item"]), block.ref)
        body = block.body.lower()
        for override in spec.get("overrides", []):
            needles = override.get("when", {}).get("contains", [])
            if any(needle.lower() in body for needle in needles):
                item.update(_substitute(copy.deepcopy(override.get("set", {})), block.ref))
                break
        items.append(item)
    return {spec.get("arrayField", "items"): items}


class RuleSet:
    def __init__(self, rules: list[Rule]) -> None:
        self.rules = rules

    @classmethod
    def from_list(cls, raw: list[dict[str, Any]]) -> RuleSet:
        rules = []
        for entry in raw:
            match = entry.get("match", {})
            rules.append(
                Rule(
                    name=str(entry.get("name", "rule")),
                    schema_title=match.get("schemaTitle"),
                    contains=list(match.get("contains", [])),
                    regex=match.get("regex"),
                    respond=dict(entry.get("respond", {})),
                    delay_ms=int(entry.get("delayMs", 0)),
                )
            )
        return cls(rules)

    @classmethod
    def from_yaml(cls, path: Path) -> RuleSet:
        return cls.from_list(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or [])

    def match(self, messages: list[dict[str, Any]], response_schema: dict[str, Any] | None) -> Rule | None:
        text = "\n".join(str(m.get("content", "")) for m in messages)
        return next((rule for rule in self.rules if rule.matches(text, response_schema)), None)

    def respond(
        self, messages: list[dict[str, Any]], response_schema: dict[str, Any] | None
    ) -> tuple[str, Any | None]:
        rule = self.match(messages, response_schema)
        if rule is None:
            if response_schema:
                instance = minimal_instance(response_schema)
                return json.dumps(instance), instance
            return "MOCK RESPONSE", None
        text = "\n".join(str(m.get("content", "")) for m in messages)
        if "perEvidence" in rule.respond:
            structured = _per_evidence(rule.respond["perEvidence"], text)
            return json.dumps(structured), structured
        if "json" in rule.respond:
            structured = copy.deepcopy(rule.respond["json"])
            return json.dumps(structured), structured
        return str(rule.respond.get("text", "")), None


def minimal_instance(schema: dict[str, Any], root: dict[str, Any] | None = None) -> Any:
    """The smallest value that satisfies a (Pydantic-style) JSON Schema."""
    root = root or schema
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return minimal_instance(target, root)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            return minimal_instance(schema[key][0], root)
    kind = schema.get("type", "object")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        properties = schema.get("properties", {})
        return {name: minimal_instance(properties.get(name, {}), root) for name in schema.get("required", [])}
    if kind == "array":
        return [minimal_instance(schema.get("items", {}), root) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "x" * int(schema.get("minLength", 0))
    if kind in {"integer", "number"}:
        return schema.get("minimum", schema.get("exclusiveMinimum", -1) + 1)
    if kind == "boolean":
        return False
    return None
