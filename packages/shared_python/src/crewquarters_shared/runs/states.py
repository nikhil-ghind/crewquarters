"""Run state machine (PLAN.md section 7.2). Pure logic; no database access."""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    LOADING_MODEL = "LOADING_MODEL"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


S = RunState

TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    S.QUEUED: frozenset({S.PREPARING, S.CANCELLED}),
    S.PREPARING: frozenset({S.RUNNING, S.FAILED, S.CANCELLING, S.INTERRUPTED}),
    S.RUNNING: frozenset(
        {S.LOADING_MODEL, S.WAITING_INPUT, S.SUCCEEDED, S.FAILED, S.CANCELLING, S.INTERRUPTED}
    ),
    S.LOADING_MODEL: frozenset({S.RUNNING, S.FAILED, S.CANCELLING, S.INTERRUPTED}),
    S.WAITING_INPUT: frozenset({S.RUNNING, S.CANCELLING, S.INTERRUPTED, S.FAILED}),
    S.CANCELLING: frozenset({S.CANCELLED}),
    S.INTERRUPTED: frozenset({S.QUEUED}),
    S.FAILED: frozenset({S.QUEUED}),
    S.SUCCEEDED: frozenset(),
    S.CANCELLED: frozenset(),
}

# States in which the run has a live (or starting) container attempt.
ACTIVE_STATES = frozenset({S.PREPARING, S.LOADING_MODEL, S.RUNNING, S.WAITING_INPUT, S.CANCELLING})
# States that count against activeTimeoutSeconds. WAITING_INPUT pauses the clock.
ACTIVE_CLOCK_STATES = frozenset({S.PREPARING, S.LOADING_MODEL, S.RUNNING})
WAIT_CLOCK_STATES = frozenset({S.WAITING_INPUT})
# States that end an attempt. FAILED and INTERRUPTED may still be retried by the owner.
TERMINAL_STATES = frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLED, S.INTERRUPTED})
FINAL_STATES = frozenset({S.SUCCEEDED, S.CANCELLED})
CANCELLABLE_STATES = frozenset({S.QUEUED, S.PREPARING, S.LOADING_MODEL, S.RUNNING, S.WAITING_INPUT})


class InvalidTransition(ValueError):
    def __init__(self, current: RunState, target: RunState) -> None:
        super().__init__(f"Run cannot move from {current} to {target}.")
        self.current = current
        self.target = target


def can_transition(current: RunState | str, target: RunState | str) -> bool:
    return RunState(target) in TRANSITIONS[RunState(current)]


def check_transition(current: RunState | str, target: RunState | str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(RunState(current), RunState(target))
