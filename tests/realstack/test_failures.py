"""Cancellation and failure injection on the real stack (PLAN.md §21, §24 step 11).

The stack runs with CQ_HEARTBEAT_TIMEOUT_SECONDS=15 (compose.realstack.yaml), so a lost
agent is detected within ~20 s instead of ~35 s. Services stopped here are restarted by the
``healthy`` fixture even when an assertion fails.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

PROBE = "contract-probe"
ADMIN_URL = "http://127.0.0.1:18084"  # the broker's port, published by compose.realstack.yaml
LOST = 60  # heartbeat timeout 15 s + reconciler interval + container stop


def _probe(owner: Any, kb: dict[str, Any], checks: list[str], **config: Any) -> dict[str, Any]:
    return dict(owner.install(PROBE, {"knowledgeBaseId": kb["id"], "checks": checks, **config}))


def _broker_up() -> bool:
    try:
        return httpx.get(f"{ADMIN_URL}/health/ready", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def _log_seen(owner: Any, run_id: str, message: str) -> bool:
    return any(
        e["type"] == "run.log" and e["payload"].get("message") == message
        for e in owner.events(run_id)
    )


def test_cancel_mid_run_stops_the_container_and_cannot_be_retried(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    installation = _probe(
        owner, knowledge_base, ["handshake", "llm", "cancellation"], cancelWaitSeconds=300
    )
    run = owner.start(installation["id"])
    owner.wait_for(
        lambda: _log_seen(owner, run["id"], "waiting for cancellation"), "the agent to wait", 90
    )
    container = stack.run_container(run["id"])
    assert stack.inspect(container)["State"]["Status"] == "running"

    cancelling = owner.cancel(run["id"])
    assert cancelling["state"] == "CANCELLING" and cancelling["cancelRequested"] is True
    done = owner.wait_state(run["id"], "CANCELLED", timeout=60)
    assert done["finishedAt"] is not None
    owner.wait_for(lambda: stack.inspect(container) is None, "the container to be removed", 30)
    # SUCCEEDED and CANCELLED are final (ADR 0006): a cancelled run is not retried.
    again = owner.post(f"/api/v1/runs/{run['id']}/retry")
    assert again.status_code == 409, again.text


def test_out_of_memory_agent_is_detected_and_reported(
    owner: Any, stack: Any, healthy: None
) -> None:
    """The agent allocates past its 64 MiB limit and the kernel kills it.

    Observed platform behaviour: nothing reads the container's exit status, so the run is
    caught by the heartbeat lease as INTERRUPTED/HEARTBEAT_LOST (retryable), not FAILED with
    an OOM code, and run_attempts.exit_code stays empty (reported in docs/testing-realstack.md).
    """
    installation = owner.install("realstack-oom", {})
    run = owner.start(installation["id"])
    container = stack.run_container(run["id"])
    owner.wait_for(
        lambda: (
            (info := stack.inspect(container)) is not None
            and info["State"]["Status"] == "exited"
            and info
        ),
        "the container to be OOM-killed",
        90,
    )
    state = stack.inspect(container)["State"]
    assert state["OOMKilled"] is True and state["ExitCode"] == 137, state
    interrupted = owner.wait_state(run["id"], "INTERRUPTED", timeout=LOST)
    assert interrupted["error"]["code"] == "HEARTBEAT_LOST", interrupted["error"]
    assert interrupted["retryable"] is True
    assert _log_seen(owner, run["id"], "allocating")


def test_broker_outage_short_is_absorbed_long_interrupts_and_retry_recovers(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    installation = _probe(owner, knowledge_base, ["handshake", "input", "llm", "knowledge"])

    # Short outage (a restart, well under the heartbeat timeout). Observed: the attempt
    # survives (no HEARTBEAT_LOST), but the SDK gives up on a broker call after ~3.5 s of
    # connection errors (4 attempts, 0.5/1/2 s backoff), so the call the agent was making
    # when the broker went away fails with a clear, retryable BROKER_UNAVAILABLE. Reported in
    # docs/testing-realstack.md: the SDK's retry budget is far shorter than the lease.
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    down = time.monotonic()
    stack.stop("capability-broker", grace=1)
    time.sleep(3)
    stack.start("capability-broker", wait=False)
    owner.wait_for(_broker_up, "the broker to answer again", 20, 0.2)
    outage = time.monotonic() - down
    assert outage < 12, f"the 'short' outage took {outage:.1f}s"
    answered = owner.answer(request, {"choice": "ok"})
    assert answered.status_code == 200, (answered.text, owner.run(run["id"]))
    ended = owner.wait_state(run["id"], {"SUCCEEDED", "FAILED"}, timeout=120)
    assert ended["currentAttempt"] == 1  # never interrupted
    if ended["state"] == "FAILED":
        checks = {c["name"]: c for c in ended["error"]["details"]["checks"]}
        assert checks["input"]["detail"].startswith("BROKER_UNAVAILABLE"), checks
        # Everything after the outage worked against the restarted broker.
        assert checks["llm"]["status"] == checks["knowledge"]["status"] == "passed"

    # Long outage: the attempt loses its heartbeat lease.
    run = owner.start(installation["id"])
    owner.pending_input(run["id"])
    stack.stop("capability-broker")
    try:
        interrupted = owner.wait_state(run["id"], "INTERRUPTED", timeout=LOST)
    finally:
        stack.start("capability-broker")
    assert interrupted["error"]["code"] == "HEARTBEAT_LOST" and interrupted["retryable"]
    owner.wait_for(
        lambda: (
            stack.inspect(stack.run_container(run["id"], 1)) is None
            or stack.inspect(stack.run_container(run["id"], 1))["State"]["Status"] == "exited"
        ),
        "attempt 1's container to stop",
        60,
    )

    # The owner retries once the broker is back: a new attempt, a new container; the same
    # input key is asked again and answered.
    retried = owner.retry(run["id"])
    assert retried["currentAttempt"] == 2
    request = owner.pending_input(run["id"])
    assert request["key"] == "probe-input-v1"
    assert owner.answer(request, {"choice": "ok"}).status_code == 200
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=120)
    assert done["currentAttempt"] == 2
    assert stack.run_containers(run["id"]) in (
        [],
        [stack.run_container(run["id"], 2)],
        sorted([stack.run_container(run["id"], 1), stack.run_container(run["id"], 2)]),
    )


def test_model_gateway_down_fails_llm_calls_visibly(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    installation = _probe(owner, knowledge_base, ["handshake", "input", "llm", "knowledge"])
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    stack.stop("model-gateway")
    try:
        assert owner.answer(request, {"choice": "ok"}).status_code == 200
        failed = owner.wait_state(run["id"], "FAILED", timeout=180)
    finally:
        stack.start("model-gateway")
    assert failed["error"]["code"] == "CONTRACT_CHECKS_FAILED", failed["error"]
    checks = {c["name"]: c for c in failed["error"]["details"]["checks"]}
    assert checks["llm"]["status"] == "failed"
    assert checks["llm"]["detail"].startswith(("MODEL_UNAVAILABLE", "PROVIDER_UNAVAILABLE")), (
        checks["llm"]
    )
    # Only the LLM failed: the knowledge service answered as usual.
    assert checks["knowledge"]["status"] == "passed" and checks["input"]["status"] == "passed"

    # With the gateway back, the same installation works again.
    run = owner.start(installation["id"])
    assert owner.answer(owner.pending_input(run["id"]), {"choice": "ok"}).status_code == 200
    owner.wait_state(run["id"], "SUCCEEDED", timeout=180)


def test_worker_restart_mid_run_keeps_one_container_and_the_run_completes(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    """ADR 0006: a worker crash neither loses nor duplicates a run's container."""
    installation = _probe(owner, knowledge_base, ["handshake", "input"])

    # Killed while the agent waits for input: the agent keeps running without the worker.
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    stack.kill("scheduler")
    try:
        assert owner.answer(request, {"choice": "ok"}).status_code == 200
        done = owner.wait_state(run["id"], "SUCCEEDED", timeout=60)
    finally:
        stack.start("scheduler")
    assert done["currentAttempt"] == 1
    assert stack.run_containers(run["id"]) in ([], [stack.run_container(run["id"])])

    # Created while no worker runs: queued, then dispatched exactly once after the restart.
    stack.kill("scheduler")
    try:
        run = owner.start(installation["id"])
        time.sleep(3)
        assert owner.run(run["id"])["state"] == "QUEUED"
        assert stack.run_containers(run["id"]) == []
    finally:
        stack.start("scheduler")
    request = owner.pending_input(run["id"])
    # Killed again right after dispatch, then restarted: the reclaimed dispatch job must
    # not start a second container for the same attempt.
    stack.kill("scheduler")
    stack.start("scheduler")
    assert owner.answer(request, {"choice": "ok"}).status_code == 200
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=120)
    assert done["currentAttempt"] == 1
    assert stack.run_containers(run["id"]) in ([], [stack.run_container(run["id"])])
