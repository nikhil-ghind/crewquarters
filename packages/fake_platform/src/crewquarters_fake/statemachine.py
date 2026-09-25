"""Run state machine: the control plane's canonical transitions (PLAN.md section 7.2)."""

from __future__ import annotations

from crewquarters_shared.runs import states

TRANSITIONS: dict[str, frozenset[str]] = {
    str(source): frozenset(str(target) for target in targets)
    for source, targets in states.TRANSITIONS.items()
}
# States in which the broker accepts agent calls. CANCELLING is handled separately: only
# capability-free calls (heartbeat, events, result) are accepted while a cancel is pending.
ACTIVE = frozenset({"PREPARING", "RUNNING", "LOADING_MODEL", "WAITING_INPUT"})
FINAL = frozenset(str(s) for s in states.FINAL_STATES)
SETTLED = frozenset(str(s) for s in states.TERMINAL_STATES)


class IllegalTransition(Exception):
    pass


def check_transition(current: str, target: str) -> None:
    if not states.can_transition(current, target):
        raise IllegalTransition(f"run cannot move from {current} to {target}")
