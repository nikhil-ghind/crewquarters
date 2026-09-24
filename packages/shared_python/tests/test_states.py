from __future__ import annotations

import itertools

import pytest

from crewquarters_shared.runs.states import (
    FINAL_STATES,
    TRANSITIONS,
    InvalidTransition,
    RunState,
    can_transition,
    check_transition,
)

pytestmark = pytest.mark.no_db

S = RunState
EXPECTED = {
    (S.QUEUED, S.PREPARING),
    (S.QUEUED, S.CANCELLED),
    (S.PREPARING, S.RUNNING),
    (S.PREPARING, S.FAILED),
    (S.PREPARING, S.CANCELLING),
    (S.PREPARING, S.INTERRUPTED),
    (S.RUNNING, S.LOADING_MODEL),
    (S.RUNNING, S.WAITING_INPUT),
    (S.RUNNING, S.SUCCEEDED),
    (S.RUNNING, S.FAILED),
    (S.RUNNING, S.CANCELLING),
    (S.RUNNING, S.INTERRUPTED),
    (S.LOADING_MODEL, S.RUNNING),
    (S.LOADING_MODEL, S.FAILED),
    (S.LOADING_MODEL, S.CANCELLING),
    (S.LOADING_MODEL, S.INTERRUPTED),
    (S.WAITING_INPUT, S.RUNNING),
    (S.WAITING_INPUT, S.CANCELLING),
    (S.WAITING_INPUT, S.INTERRUPTED),
    (S.WAITING_INPUT, S.FAILED),
    (S.CANCELLING, S.CANCELLED),
    (S.INTERRUPTED, S.QUEUED),
    (S.FAILED, S.QUEUED),
}


@pytest.mark.parametrize(("current", "target"), list(itertools.product(S, S)))
def test_every_transition_matches_plan(current: RunState, target: RunState) -> None:
    allowed = (current, target) in EXPECTED
    assert can_transition(current, target) is allowed
    if not allowed:
        with pytest.raises(InvalidTransition):
            check_transition(current, target)


def test_final_states_have_no_exits() -> None:
    for state in FINAL_STATES:
        assert TRANSITIONS[state] == frozenset()


def test_every_state_is_reachable_from_queued() -> None:
    seen, frontier = {S.QUEUED}, [S.QUEUED]
    while frontier:
        for nxt in TRANSITIONS[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(S)
