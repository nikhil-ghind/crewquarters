import pytest
from pydantic import ValidationError

from caller_agent.config import CallerConfig


def test_defaults_and_derived_ranges() -> None:
    config = CallerConfig.model_validate({"spreadsheetId": "s1"})
    assert (config.max_calls, config.response_seconds, config.call_timeout_seconds) == (3, 20, 180)
    assert config.input_start_row == 2
    assert config.result_tab == "Results"
    assert "{name}" in config.script
    assert config.disclosure


def test_spreadsheet_id_is_required() -> None:
    with pytest.raises(ValidationError):
        CallerConfig.model_validate({})


@pytest.mark.parametrize("script", ["Hello {first_name}", "Hi {0}", "Call {name} and {secret}"])
def test_script_may_only_use_the_name_placeholder(script: str) -> None:
    with pytest.raises(ValidationError) as info:
        CallerConfig.model_validate({"spreadsheetId": "s1", "script": script})
    assert "{name}" in str(info.value)


def test_script_without_placeholders_is_fine() -> None:
    assert (
        CallerConfig.model_validate({"spreadsheetId": "s1", "script": "Hello there."}).script
        == "Hello there."
    )


@pytest.mark.parametrize("value", ["Results!A1:H", "Results!B:I", "Results", "Results!A:G"])
def test_result_range_must_be_a_full_a_to_h_tab(value: str) -> None:
    with pytest.raises(ValidationError):
        CallerConfig.model_validate({"spreadsheetId": "s1", "resultRange": value})


def test_quoted_result_tab() -> None:
    config = CallerConfig.model_validate(
        {"spreadsheetId": "s1", "resultRange": "'Call Results'!A:H"}
    )
    assert config.result_tab == "'Call Results'"


@pytest.mark.parametrize(
    ("value", "row"), [("Contacts!A2:D", 2), ("Contacts!A1:D50", 1), ("'My List'!A5:D", 5)]
)
def test_input_range_start_row(value: str, row: int) -> None:
    assert (
        CallerConfig.model_validate({"spreadsheetId": "s1", "inputRange": value}).input_start_row
        == row
    )


@pytest.mark.parametrize("value", ["Contacts!A:D", "Contacts!B2:E", "Contacts"])
def test_input_range_must_start_at_a_row_in_column_a(value: str) -> None:
    with pytest.raises(ValidationError):
        CallerConfig.model_validate({"spreadsheetId": "s1", "inputRange": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maxCalls", 0),
        ("maxCalls", 11),
        ("responseSeconds", 4),
        ("callPollSeconds", 0.01),
        ("timezone", "IST"),
    ],
)
def test_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CallerConfig.model_validate({"spreadsheetId": "s1", field: value})


@pytest.mark.parametrize(
    ("input_range", "result_range"),
    [
        ("Contacts!A2:D", "Contacts!A:H"),
        ("'My List'!A2:D", "'My List'!A:H"),
        ("'Contacts'!A2:D", "Contacts!A:H"),
    ],
)
def test_results_cannot_overwrite_the_contacts_tab(input_range: str, result_range: str) -> None:
    with pytest.raises(ValidationError) as info:
        CallerConfig.model_validate(
            {"spreadsheetId": "s1", "inputRange": input_range, "resultRange": result_range}
        )
    assert "different tab" in str(info.value)
