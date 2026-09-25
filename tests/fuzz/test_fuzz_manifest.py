"""Manifests, configuration schemas and configuration forms never crash validation.

Every property is: the validator either accepts (and returns a normalized value) or raises
``PlatformError`` with a 4xx status. Any other exception would surface as a 500 when an
operator imports a manifest or saves an agent's configuration.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from crewquarters_shared.errors import PlatformError
from crewquarters_shared.manifest import (
    resolve_model_bindings,
    validate_config,
    validate_manifest,
)
from crewquarters_shared.schema_guard import check_schema

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> Any:
    # Source manifests carry a placeholder digest that `crewctl build --push` fills in.
    return yaml.safe_load(path.read_text().replace("REQUIRED_DIGEST", "a" * 64))


MANIFESTS = [
    _load(p)
    for p in sorted([*ROOT.glob("agents/*/manifest.yaml"), *ROOT.glob("catalog/dev/*.yaml")])
]

SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**70), max_value=2**70)
    | st.floats(allow_nan=False)
    | st.text(max_size=40)
)
JSON = st.recursive(
    SCALARS,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4)
    ),
    max_leaves=25,
)
SCHEMA_KEYS = st.sampled_from(
    [
        "type",
        "properties",
        "items",
        "required",
        "enum",
        "const",
        "default",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "patternProperties",
        "additionalProperties",
        "format",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "$ref",
        "$defs",
        "if",
        "then",
        "else",
        "x-crewquarters-widget",
    ]
)
SCHEMAS = st.recursive(
    st.fixed_dictionaries({}, optional={"type": st.sampled_from(["string", "object", "x"])}),
    lambda children: st.dictionaries(SCHEMA_KEYS, children | JSON, max_size=5),
    max_leaves=15,
)


def _paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    out = [prefix]
    if isinstance(node, dict):
        for key, value in node.items():
            out.extend(_paths(value, (*prefix, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_paths(value, (*prefix, index)))
    return out


def _replace(doc: Any, path: tuple[Any, ...], value: Any) -> Any:
    if not path:
        return value
    doc = copy.deepcopy(doc)
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return doc


@st.composite
def mutated_manifests(draw: st.DrawFn) -> Any:
    """A bundled manifest with one to three nodes replaced by arbitrary JSON."""
    doc = draw(st.sampled_from(MANIFESTS))
    for _ in range(draw(st.integers(1, 3))):
        paths = _paths(doc)
        doc = _replace(doc, draw(st.sampled_from(paths)), draw(JSON | SCHEMAS))
    return doc


def _accepts_or_rejects(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except PlatformError as exc:
        assert 400 <= exc.status_code < 500, exc
        return None


def test_bundled_manifests_are_valid() -> None:
    assert len(MANIFESTS) >= 4
    for manifest in MANIFESTS:
        validate_manifest(manifest)


@given(mutated_manifests())
def test_mutated_manifest_is_accepted_or_rejected(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        return  # the API's request model only passes objects
    normalized = _accepts_or_rejects(validate_manifest, manifest)
    if normalized is not None:  # an accepted manifest is fully normalized
        spec = normalized["spec"]
        assert "configurationSchema" in spec and "maxInputWaitSeconds" in spec["resources"]


@given(st.dictionaries(st.text(max_size=12), JSON, max_size=6))
def test_arbitrary_object_is_rejected(manifest: dict[str, Any]) -> None:
    _accepts_or_rejects(validate_manifest, manifest)


@given(SCHEMAS, st.booleans())
def test_schema_guard_accepts_or_rejects(schema: dict[str, Any], allow_patterns: bool) -> None:
    _accepts_or_rejects(
        lambda: check_schema(schema, code="INVALID_SCHEMA", allow_patterns=allow_patterns)
    )


@given(st.text(max_size=60))
def test_schema_guard_never_compiles_a_bad_pattern(pattern: str) -> None:
    """A pattern either fails the guard or compiles; jsonschema would otherwise raise
    ``re.error`` while validating a configuration."""
    import re

    schema = {"type": "object", "properties": {"x": {"type": "string", "pattern": pattern}}}
    if _accepts_or_rejects(
        lambda: check_schema(schema, code="INVALID_SCHEMA", allow_patterns=True) or True
    ):
        re.compile(pattern)


@given(st.sampled_from(MANIFESTS), st.dictionaries(st.text(max_size=20), JSON, max_size=8))
def test_configuration_form_accepts_or_rejects(manifest: Any, config: dict[str, Any]) -> None:
    normalized = validate_manifest(manifest)
    bindings = resolve_model_bindings(normalized["spec"]["permissions"]["llmProfiles"])
    filled = _accepts_or_rejects(validate_config, normalized, config, bindings)
    if filled is not None:
        assert set(config) <= set(filled)


@st.composite
def schema_shaped_configs(draw: st.DrawFn) -> tuple[Any, dict[str, Any]]:
    """Configurations that use the manifest's own property names with arbitrary values,
    which reach deeper into each property's constraints than random keys do."""
    manifest = draw(st.sampled_from(MANIFESTS))
    names = list((manifest["spec"].get("configurationSchema") or {}).get("properties") or {})
    if not names:
        return manifest, {}
    keys = st.sampled_from(names)
    return manifest, draw(st.dictionaries(keys, JSON, max_size=len(names)))


@given(schema_shaped_configs())
def test_schema_shaped_configuration_accepts_or_rejects(
    case: tuple[Any, dict[str, Any]],
) -> None:
    manifest, config = case
    normalized = validate_manifest(manifest)
    bindings = resolve_model_bindings(normalized["spec"]["permissions"]["llmProfiles"])
    _accepts_or_rejects(validate_config, normalized, config, bindings)


@given(
    st.lists(st.text(max_size=30), max_size=4),
    st.dictionaries(st.text(max_size=30), st.text(max_size=30), max_size=4),
)
def test_model_bindings_accept_or_reject(profiles: list[str], choices: dict[str, str]) -> None:
    _accepts_or_rejects(resolve_model_bindings, profiles, choices)
