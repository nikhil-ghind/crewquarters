import pytest

from crewquarters_fake.statemachine import ACTIVE, TRANSITIONS, IllegalTransition, check_transition

# Edges copied from the PLAN.md section 7.2 state diagram.
PLAN_EDGES = {
    ("QUEUED", "PREPARING"),
    ("QUEUED", "CANCELLED"),
    ("PREPARING", "RUNNING"),
    ("PREPARING", "FAILED"),
    ("PREPARING", "CANCELLING"),
    ("RUNNING", "LOADING_MODEL"),
    ("LOADING_MODEL", "RUNNING"),
    ("LOADING_MODEL", "FAILED"),
    ("LOADING_MODEL", "CANCELLING"),
    ("RUNNING", "WAITING_INPUT"),
    ("WAITING_INPUT", "RUNNING"),
    ("WAITING_INPUT", "CANCELLING"),
    ("RUNNING", "SUCCEEDED"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "CANCELLING"),
    ("CANCELLING", "CANCELLED"),
    ("PREPARING", "INTERRUPTED"),
    ("RUNNING", "INTERRUPTED"),
    ("LOADING_MODEL", "INTERRUPTED"),
    ("WAITING_INPUT", "INTERRUPTED"),
    ("INTERRUPTED", "QUEUED"),
    ("FAILED", "QUEUED"),
}


def test_transition_table_matches_plan() -> None:
    edges = {(src, dst) for src, dsts in TRANSITIONS.items() for dst in dsts}
    assert edges == PLAN_EDGES


def test_final_states_have_no_exits() -> None:
    assert TRANSITIONS["SUCCEEDED"] == frozenset()
    assert TRANSITIONS["CANCELLED"] == frozenset()


def test_active_states() -> None:
    assert {"PREPARING", "RUNNING", "LOADING_MODEL", "WAITING_INPUT"} == ACTIVE


def test_illegal_transition_raises() -> None:
    check_transition("RUNNING", "SUCCEEDED")
    with pytest.raises(IllegalTransition):
        check_transition("WAITING_INPUT", "SUCCEEDED")
    with pytest.raises(IllegalTransition):
        check_transition("SUCCEEDED", "QUEUED")
