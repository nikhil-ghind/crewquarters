import json
from typing import Literal

from jsonschema import validate
from pydantic import BaseModel

from crewquarters.untrusted import evidence
from crewquarters_fake.llm_rules import RuleSet, minimal_instance


class Item(BaseModel):
    ref: str
    priority: Literal["urgent", "important", "low"]
    reason: str
    uncertain: bool


class Batch(BaseModel):
    items: list[Item]


def test_per_evidence_emits_one_item_per_block_with_overrides() -> None:
    rules = RuleSet.from_list(
        [
            {
                "name": "digest",
                "match": {"schemaTitle": "Batch"},
                "respond": {
                    "perEvidence": {
                        "arrayField": "items",
                        "item": {"ref": "{ref}", "priority": "low", "reason": "Routine", "uncertain": False},
                        "overrides": [
                            {
                                "when": {"contains": ["outage", "down"]},
                                "set": {"priority": "urgent", "reason": "Outage"},
                            },
                            {"when": {"contains": ["invoice"]}, "set": {"priority": "important"}},
                        ],
                    }
                },
            }
        ]
    )
    prompt = "\n".join(
        [
            evidence("The API is DOWN since 9am", ref="m1", source="gmail", boundary="aa"),
            evidence("Invoice attached", ref="m2", source="gmail", boundary="aa"),
            evidence("Weekly newsletter", ref="m3", source="gmail", boundary="aa"),
        ]
    )
    text, structured = rules.respond([{"role": "user", "content": prompt}], Batch.model_json_schema())
    assert structured == json.loads(text)
    assert structured == {
        "items": [
            {"ref": "m1", "priority": "urgent", "reason": "Outage", "uncertain": False},
            {"ref": "m2", "priority": "important", "reason": "Routine", "uncertain": False},
            {"ref": "m3", "priority": "low", "reason": "Routine", "uncertain": False},
        ]
    }


def test_text_and_json_rules_match_on_contains_and_regex() -> None:
    rules = RuleSet.from_list(
        [
            {"name": "json", "match": {"regex": r"capital of \w+"}, "respond": {"json": {"answer": "Paris"}}},
            {"name": "hello", "match": {"contains": ["hello"]}, "respond": {"text": "Hi there"}},
        ]
    )
    assert rules.respond([{"role": "user", "content": "Say HELLO"}], None) == ("Hi there", None)
    _text, structured = rules.respond([{"role": "user", "content": "What is the capital of France?"}], None)
    assert structured == {"answer": "Paris"}


def test_no_match_returns_mock_text_or_minimal_instance() -> None:
    rules = RuleSet.from_list([])
    assert rules.respond([{"role": "user", "content": "x"}], None) == ("MOCK RESPONSE", None)
    schema = Batch.model_json_schema()
    _, structured = rules.respond([{"role": "user", "content": "x"}], schema)
    validate(structured, schema)


def test_minimal_instance_handles_refs_enums_and_bounds() -> None:
    schema = {
        "type": "object",
        "required": ["name", "count", "kind", "tags", "nested"],
        "properties": {
            "name": {"type": "string", "minLength": 3},
            "count": {"type": "integer", "minimum": 2},
            "kind": {"enum": ["a", "b"]},
            "tags": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "nested": {"$ref": "#/$defs/N"},
            "optional": {"type": "string"},
        },
        "$defs": {"N": {"type": "object", "required": ["flag"], "properties": {"flag": {"type": "boolean"}}}},
    }
    instance = minimal_instance(schema)
    validate(instance, schema)
    assert "optional" not in instance  # type: ignore[operator]


def test_rule_delay_is_reported() -> None:
    rules = RuleSet.from_list([{"name": "slow", "match": {}, "respond": {"text": "ok"}, "delayMs": 25}])
    assert rules.match([{"role": "user", "content": "x"}], None).delay_ms == 25  # type: ignore[union-attr]
