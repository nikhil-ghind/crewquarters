"""Capability tokens: every token string, and every signed claim set, is either verified or
rejected with ``jwt.InvalidTokenError``, which the broker and gateway turn into a 401. Any
other exception would be a 500 on the agent-facing API."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import pytest
from hypothesis import given
from hypothesis import strategies as st

from crewquarters_shared import capability

pytestmark = pytest.mark.no_db

KEY = "fuzz-signing-key-" + "0123456789abcdef" * 4
SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**70), max_value=2**70)
    | st.floats(allow_nan=False)
    | st.text(max_size=20)
)
JSON = st.recursive(
    SCALARS,
    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=5), c, max_size=3),
    max_leaves=8,
)
CLAIM_NAMES = ["run", "att", "ins", "ver", "cap", "res", "iat", "exp", "jti", "sub", "nbf"]


def _valid_claims() -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": capability.ISSUER,
        "aud": capability.AUDIENCE,
        "jti": uuid.uuid4().hex,
        "sub": "run:x",
        "iat": now,
        "exp": now + 600,
        "run": str(uuid.uuid4()),
        "att": 1,
        "ins": str(uuid.uuid4()),
        "ver": str(uuid.uuid4()),
        "cap": ["events.write"],
        "res": {},
    }


def _verify(token: str) -> capability.CapabilityClaims | None:
    try:
        return capability.verify(token, KEY)
    except jwt.InvalidTokenError:
        return None


@given(st.text(max_size=300))
def test_random_text_is_rejected(token: str) -> None:
    assert _verify(token) is None


@given(st.lists(st.text(alphabet="ABCDEFabcdef0123456789-_", max_size=60), min_size=3, max_size=3))
def test_random_three_part_token_is_rejected(parts: list[str]) -> None:
    assert _verify(".".join(parts)) is None


@given(
    st.lists(st.sampled_from(CLAIM_NAMES), unique=True, max_size=4),
    st.dictionaries(st.sampled_from(CLAIM_NAMES), JSON, max_size=4),
)
def test_signed_claims_with_missing_or_wrong_types_are_rejected(
    drop: list[str], overrides: dict[str, Any]
) -> None:
    """Even a correctly signed token with malformed claims is a 401, never a crash."""
    claims = _valid_claims()
    for name in drop:
        claims.pop(name, None)
    claims.update(overrides)
    try:
        token = jwt.encode(claims, KEY, algorithm=capability.ALGORITHM)
    except (TypeError, ValueError):
        return  # PyJWT cannot even encode it
    verified = _verify(token)
    if verified is not None:
        assert isinstance(verified.attempt, int)
        assert all(isinstance(c, str) for c in verified.capabilities)
        assert isinstance(verified.resources, dict)


@given(st.text(min_size=1, max_size=40))
def test_wrong_key_is_rejected(other: str) -> None:
    token = jwt.encode(_valid_claims(), KEY + other, algorithm=capability.ALGORITHM)
    assert _verify(token) is None


@given(st.sampled_from(["none", "HS384", "HS512"]))
def test_other_algorithms_are_rejected(algorithm: str) -> None:
    token = jwt.encode(_valid_claims(), None if algorithm == "none" else KEY, algorithm=algorithm)
    assert _verify(token) is None


@given(
    st.lists(st.text(max_size=30), max_size=5),
    st.integers(min_value=1, max_value=1000),
    st.integers(min_value=1, max_value=86_400),
)
def test_minted_tokens_round_trip(caps: list[str], attempt: int, ttl: int) -> None:
    run_id, install, version = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token, claims = capability.mint(
        signing_key=KEY,
        run_id=run_id,
        attempt=attempt,
        installation_id=install,
        agent_version_id=version,
        capabilities=caps,
        ttl_seconds=ttl,
    )
    verified = capability.verify(token, KEY)
    assert verified.run_id == str(run_id) and verified.attempt == attempt
    assert verified.capabilities == sorted(caps) == claims.capabilities


@pytest.mark.parametrize(
    "change",
    [
        {"run": None},
        {"att": "1"},
        {"att": True},
        {"cap": "llm.profile:local.general.small"},
        {"cap": [1]},
        {"res": ["a"]},
        {"iat": 10**20},
    ],
)
def test_regression_signed_malformed_claims_are_invalid_tokens(change: dict[str, Any]) -> None:
    """Found by the property above: a signed token without ``run`` raised ``KeyError`` (a
    500 in the broker and gateway); a string ``cap`` was split into characters."""
    claims = {k: v for k, v in {**_valid_claims(), **change}.items() if v is not None}
    try:
        token = jwt.encode(claims, KEY, algorithm=capability.ALGORITHM)
    except (TypeError, ValueError):
        pytest.skip("PyJWT refuses to encode these claims")
    with pytest.raises(jwt.InvalidTokenError):
        capability.verify(token, KEY)
