from __future__ import annotations

import pytest
from pydantic import ValidationError
from voice_helpers import make_config


def test_defaults() -> None:
    config = make_config()
    assert (config.agent_name, config.max_calls, config.max_call_seconds) == ("Sam", 5, 240)
    assert config.disclosure == "an automated AI assistant"
    assert (config.input_tab, config.input_start_row, config.result_tab) == (
        "Contacts",
        2,
        "Results",
    )


def test_call_cap_is_bounded() -> None:
    with pytest.raises(ValidationError):
        make_config(maxCalls=26)


@pytest.mark.parametrize("disclosure", ["a friendly caller", "Sam from the front desk", ""])
def test_disclosure_must_say_the_caller_is_automated(disclosure: str) -> None:
    with pytest.raises(ValidationError):
        make_config(disclosure=disclosure)


@pytest.mark.parametrize(
    "disclosure", ["an automated assistant", "Acme's AI receptionist", "a virtual assistant"]
)
def test_honest_disclosures_are_accepted(disclosure: str) -> None:
    assert make_config(disclosure=disclosure).disclosure == disclosure


def test_results_cannot_share_the_contacts_tab() -> None:
    with pytest.raises(ValidationError):
        make_config(resultRange="Contacts!A:J")


def test_ranges_are_checked() -> None:
    with pytest.raises(ValidationError):
        make_config(inputRange="Contacts!A2:C")
    with pytest.raises(ValidationError):
        make_config(resultRange="Results!A:H")


def test_blank_brief_items_are_dropped_and_long_ones_rejected() -> None:
    assert make_config(questions=["  ", "Is Tuesday fine?"]).questions == ["Is Tuesday fine?"]
    with pytest.raises(ValidationError):
        make_config(talkingPoints=["x" * 301])
