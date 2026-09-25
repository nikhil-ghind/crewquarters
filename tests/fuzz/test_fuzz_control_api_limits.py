"""HTTP body limits and hostile JSON on the control API: always 4xx, never 500.

Covers the public owner API (with a session and CSRF header) and the internal API that
agents reach through the broker (with the service token): oversized bodies (declared and
chunked), malformed and non-JSON bodies, deeply nested JSON, and arbitrary JSON values in
the fields that accept free-form objects (agent configuration, event payloads, previews,
answers, results, action results).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import install_hello
from crewquarters_api.json_guard import MAX_JSON_DEPTH

pytestmark = pytest.mark.usefixtures("catalog_synced")

SERVICE = {"Authorization": "Bearer dev-insecure-internal-token-change-me-0000"}
JSON_HEADERS = {"content-type": "application/json"}

SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**80), max_value=2**80)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=30)
)
JSON = st.recursive(
    SCALARS,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=10), children, max_size=4)
    ),
    max_leaves=20,
)
MALFORMED = st.one_of(
    st.binary(max_size=200),
    st.text(max_size=200).map(str.encode),
    st.sampled_from(
        [
            b"",
            b"{",
            b"[",
            b'{"a":',
            b"NaN",
            b"Infinity",
            b'{"attempt": 1e999}',
            b'{"attempt": 1, "attempt": "x"}',
            b"\xff\xfe{\x00}",
            b'"\\ud800"',
            b"1" * 5000,
            b"[" * 100_000,
            b"{" * 100_000,
        ]
    ),
)


def nested(depth: int, leaf: Any = "x") -> Any:
    value = leaf
    for _ in range(depth):
        value = {"k": [value]}
    return value


def nested_text(depth: int) -> str:
    """``json.dumps`` recurses in C, so build deep documents as text."""
    return '{"k":[' * depth + '"x"' + "]}" * depth


def assert_client_error(response: httpx.Response) -> None:
    assert 400 <= response.status_code < 500, (response.status_code, response.text[:300])
    assert response.json()["error"]["code"]


@pytest.fixture
async def run_id(owner: httpx.AsyncClient, sessions: Any, settings: Any) -> AsyncIterator[str]:
    """A RUNNING run whose agent is the test (the runtime never starts anything)."""
    from crewquarters_scheduler.worker import Worker

    class InertRuntime:
        async def start_run(self, spec: Any) -> str:
            return f"inert-{spec.run_id}-{spec.attempt}"

    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    worker = Worker(sessions, InertRuntime(), settings, worker_id="fuzz")  # type: ignore[arg-type]
    assert await worker.run_once()
    base = f"/internal/v1/runs/{run['id']}"
    handshake = await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    assert handshake.status_code == 200, handshake.text
    yield str(run["id"])


# --- Size limits ---------------------------------------------------------------------------


async def test_declared_oversized_body_is_413(owner: httpx.AsyncClient, settings: Any) -> None:
    body = b"{" + b" " * settings.max_body_bytes + b"}"
    response = await owner.post("/api/v1/runs", content=body, headers=JSON_HEADERS)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_chunked_oversized_body_is_413(owner: httpx.AsyncClient, settings: Any) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(settings.max_body_bytes // 65536 + 2):
            yield b" " * 65536

    response = await owner.post("/api/v1/runs", content=chunks(), headers=JSON_HEADERS)
    assert response.status_code == 413


async def test_oversized_internal_body_is_413(owner: httpx.AsyncClient, settings: Any) -> None:
    body = json.dumps({"attempt": 1, "payload": {"x": "y" * settings.max_body_bytes}})
    response = await owner.post(
        f"/internal/v1/runs/{uuid.uuid4()}/events",
        content=body,
        headers={**SERVICE, **JSON_HEADERS},
    )
    assert response.status_code == 413


@pytest.mark.parametrize("value", ["-1", "abc", "1e9", " 12", "99999999999999999999999"])
async def test_nonsense_content_length_is_not_a_500(owner: httpx.AsyncClient, value: str) -> None:
    transport = owner._transport  # send a raw request with a lying Content-Length
    request = owner.build_request("POST", "/api/v1/runs", content=b"{}", headers=JSON_HEADERS)
    request.headers["content-length"] = value
    try:
        response = await transport.handle_async_request(request)
    except Exception:  # the ASGI test transport may refuse the header outright
        return
    await response.aread()
    assert response.status_code < 500


# --- Malformed bodies ----------------------------------------------------------------------

PUBLIC_POSTS = [
    "/api/v1/runs",
    "/api/v1/agent-installations",
    "/api/v1/schedules",
    "/api/v1/login",
    "/api/v1/bootstrap",
]


@given(st.sampled_from(PUBLIC_POSTS), MALFORMED)
async def test_malformed_public_body_is_4xx(
    owner: httpx.AsyncClient, path: str, body: bytes
) -> None:
    response = await owner.post(path, content=body, headers=JSON_HEADERS)
    assert_client_error(response)


@given(st.sampled_from(PUBLIC_POSTS), JSON)
async def test_arbitrary_json_public_body_is_4xx(
    owner: httpx.AsyncClient, path: str, body: Any
) -> None:
    response = await owner.post(path, json=body)
    assert response.status_code < 500, (response.status_code, response.text[:300])


@given(st.dictionaries(st.text(max_size=12), JSON, max_size=6))
async def test_installation_config_is_accepted_or_4xx(
    owner: httpx.AsyncClient, config: dict[str, Any]
) -> None:
    from conftest import HELLO_PERMISSIONS

    response = await owner.post(
        "/api/v1/agent-installations",
        json={"agentId": "hello-crew", "config": config, "approvedPermissions": HELLO_PERMISSIONS},
    )
    assert response.status_code < 500, (response.status_code, response.text[:300])


# --- Internal (agent-facing) API -----------------------------------------------------------

INTERNAL_POSTS = [
    "events",
    "event-batches",
    "heartbeat",
    "result",
    "input-requests",
    "model-state",
    "actions/fuzz-key/claim",
    "actions/fuzz-key/complete",
]


@given(st.sampled_from(INTERNAL_POSTS), MALFORMED)
async def test_malformed_internal_body_is_4xx(
    owner: httpx.AsyncClient, run_id: str, path: str, body: bytes
) -> None:
    response = await owner.post(
        f"/internal/v1/runs/{run_id}/{path}", content=body, headers={**SERVICE, **JSON_HEADERS}
    )
    assert_client_error(response)


EVENT_TYPES = st.sampled_from(["run.log", "run.progress", "run.metric", "run.artifact", "run.x"])


@given(st.dictionaries(st.text(max_size=10), JSON, max_size=6), EVENT_TYPES)
async def test_event_payload_is_stored_or_4xx(
    owner: httpx.AsyncClient, run_id: str, payload: dict[str, Any], event_type: str
) -> None:
    base = f"/internal/v1/runs/{run_id}"
    single = await owner.post(
        f"{base}/events",
        json={"attempt": 1, "type": event_type, "payload": payload},
        headers=SERVICE,
    )
    assert single.status_code < 500, (single.status_code, single.text[:300])
    batch = await owner.post(
        f"{base}/event-batches",
        json={
            "attempt": 1,
            "events": [{"clientEventId": uuid.uuid4().hex, "type": event_type, "payload": payload}],
        },
        headers=SERVICE,
    )
    assert batch.status_code < 500, (batch.status_code, batch.text[:300])


@given(
    st.dictionaries(st.text(max_size=10), JSON, max_size=5),
    st.one_of(st.none(), st.dictionaries(st.text(max_size=10), JSON, max_size=4)),
)
async def test_input_request_schema_and_preview_are_accepted_or_4xx(
    owner: httpx.AsyncClient, run_id: str, schema: dict[str, Any], preview: Any
) -> None:
    response = await owner.post(
        f"/internal/v1/runs/{run_id}/input-requests",
        json={
            "attempt": 1,
            "key": uuid.uuid4().hex,
            "title": "t",
            "prompt": "p",
            "schema": schema,
            "timeoutSeconds": 60,
            "preview": preview,
        },
        headers=SERVICE,
    )
    assert response.status_code < 500, (response.status_code, response.text[:300])


@given(JSON)
async def test_action_result_is_stored_or_4xx(
    owner: httpx.AsyncClient, run_id: str, result: Any
) -> None:
    base = f"/internal/v1/runs/{run_id}/actions/{uuid.uuid4().hex}"
    claim = await owner.post(f"{base}/claim", json={"attempt": 1}, headers=SERVICE)
    assert claim.status_code == 200, claim.text
    done = await owner.post(
        f"{base}/complete", json={"attempt": 1, "result": result}, headers=SERVICE
    )
    assert done.status_code < 500, (done.status_code, done.text[:300])


# --- Deep nesting --------------------------------------------------------------------------

DEPTHS = [8, 64, 200, 490, 900, 1500, 2000, 4000, 100_000]


@pytest.mark.parametrize("depth", DEPTHS)
async def test_deeply_nested_event_payload_is_not_a_500(
    owner: httpx.AsyncClient, run_id: str, depth: int
) -> None:
    base = f"/internal/v1/runs/{run_id}"
    payload = '{"level":"info","message":"deep","fields":' + nested_text(depth) + "}"
    body = '{"attempt":1,"type":"run.log","payload":' + payload + "}"
    single = await owner.post(f"{base}/events", content=body, headers={**SERVICE, **JSON_HEADERS})
    expected = 201 if depth < MAX_JSON_DEPTH // 2 - 2 else 422
    assert single.status_code == expected, (single.status_code, single.text[:300])
    batch_body = (
        '{"attempt":1,"events":[{"clientEventId":"'
        + uuid.uuid4().hex
        + '","type":"run.log","payload":'
        + payload
        + "}]}"
    )
    batch = await owner.post(
        f"{base}/event-batches", content=batch_body, headers={**SERVICE, **JSON_HEADERS}
    )
    assert batch.status_code == (200 if expected == 201 else 422), (batch.status_code, batch.text)


@pytest.mark.parametrize("depth", DEPTHS)
async def test_deeply_nested_owner_bodies_are_not_a_500(
    owner: httpx.AsyncClient, run_id: str, depth: int
) -> None:
    from conftest import HELLO_PERMISSIONS

    install = (
        '{"agentId":"hello-crew","approvedPermissions":'
        + json.dumps(HELLO_PERMISSIONS)
        + ',"config":'
        + nested_text(depth)
        + "}"
    )
    response = await owner.post(
        "/api/v1/agent-installations", content=install, headers=JSON_HEADERS
    )
    assert_client_error(response)
    base = f"/internal/v1/runs/{run_id}"
    for path, body in (
        (
            "input-requests",
            '{"attempt":1,"key":"deep","title":"t","prompt":"p","timeoutSeconds":60,'
            '"schema":{"type":"object"},"preview":' + nested_text(depth) + "}",
        ),
        (
            "input-requests",
            '{"attempt":1,"key":"deep2","title":"t","prompt":"p","timeoutSeconds":60,'
            '"schema":' + nested_text(depth) + "}",
        ),
        ("actions/deep/claim", '{"attempt":1}'),
        ("actions/deep/complete", '{"attempt":1,"result":' + nested_text(depth) + "}"),
        ("result", '{"attempt":1,"status":"succeeded","result":' + nested_text(depth) + "}"),
    ):
        response = await owner.post(
            f"{base}/{path}", content=body, headers={**SERVICE, **JSON_HEADERS}
        )
        assert response.status_code < 500, (path, depth, response.status_code, response.text[:300])


async def test_nul_characters_are_rejected_not_stored(
    owner: httpx.AsyncClient, run_id: str
) -> None:
    """PostgreSQL cannot store U+0000 in jsonb or text; it used to fail the insert (500)."""
    base = f"/internal/v1/runs/{run_id}"
    for body in (
        {"attempt": 1, "type": "run.log", "payload": {"level": "info", "message": "a\x00b"}},
        {"attempt": 1, "type": "run.log", "payload": {"k\x00": "v"}},
    ):
        response = await owner.post(f"{base}/events", json=body, headers=SERVICE)
        assert response.status_code == 422, response.text
        assert "NUL" in response.text


# --- Paths and query strings ---------------------------------------------------------------

QUERY_KEYS = st.sampled_from(
    ["state", "limit", "cursor", "after", "before", "agentId", "installationId", "action", "q"]
)
QUERY = st.dictionaries(QUERY_KEYS, st.text(max_size=30), max_size=4)
SEGMENT = st.text(max_size=40)
GETS = st.sampled_from(
    [
        "/api/v1/runs",
        "/api/v1/audit-events",
        "/api/v1/agent-installations",
        "/api/v1/input-requests",
        "/api/v1/schedules",
        "/api/v1/attention",
        "/api/v1/catalog/agents",
        "/api/v1/catalog/agents/{seg}",
        "/api/v1/catalog/agents/hello-crew/versions/{seg}",
        "/api/v1/runs/{seg}",
        "/api/v1/runs/{seg}/events/history",
        "/api/v1/agent-installations/{seg}",
        "/api/v1/schedules/{seg}",
    ]
)


@given(GETS, SEGMENT, QUERY)
async def test_hostile_paths_and_queries_are_not_a_500(
    owner: httpx.AsyncClient, template: str, segment: str, query: dict[str, str]
) -> None:
    from urllib.parse import quote

    path = template.format(seg=quote(segment, safe=""))
    response = await owner.get(path, params=query)
    assert response.status_code < 500, (path, query, response.status_code, response.text[:300])


@given(st.text(min_size=1, max_size=80))
async def test_hostile_action_keys_are_not_a_500(
    owner: httpx.AsyncClient, run_id: str, key: str
) -> None:
    from urllib.parse import quote

    base = f"/internal/v1/runs/{run_id}/actions/{quote(key, safe='')}"
    claim = await owner.post(f"{base}/claim", json={"attempt": 1}, headers=SERVICE)
    assert claim.status_code < 500, (key, claim.status_code, claim.text[:300])
