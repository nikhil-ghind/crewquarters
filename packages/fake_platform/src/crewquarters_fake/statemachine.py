"""Run state machine from PLAN.md section 7.2."""

from __future__ import annotations

TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"PREPARING", "CANCELLED"}),
    "PREPARING": frozenset({"RUNNING", "FAILED", "CANCELLING", "INTERRUPTED"}),
    "RUNNING": frozenset(
        {"LOADING_MODEL", "WAITING_INPUT", "SUCCEEDED", "FAILED", "CANCELLING", "INTERRUPTED"}
    ),
    "LOADING_MODEL": frozenset({"RUNNING", "FAILED", "CANCELLING", "INTERRUPTED"}),
    "WAITING_INPUT": frozenset({"RUNNING", "CANCELLING", "INTERRUPTED"}),
    "CANCELLING": frozenset({"CANCELLED"}),
    "INTERRUPTED": frozenset({"QUEUED"}),
    "FAILED": frozenset({"QUEUED"}),
    "SUCCEEDED": frozenset(),
    "CANCELLED": frozenset(),
}

ACTIVE = frozenset({"PREPARING", "RUNNING", "LOADING_MODEL", "WAITING_INPUT"})
FINAL = frozenset({"SUCCEEDED", "CANCELLED"})
SETTLED = frozenset({"SUCCEEDED", "CANCELLED", "FAILED", "INTERRUPTED"})


class IllegalTransition(Exception):
    pass


def check_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS[current]:
        raise IllegalTransition(f"run cannot move from {current} to {target}")
