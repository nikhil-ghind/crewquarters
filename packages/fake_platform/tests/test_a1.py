import pytest

from crewquarters_fake.providers.a1 import (
    A1Range,
    col_index,
    col_letters,
    format_range,
    parse_range,
)


def test_column_letters_round_trip() -> None:
    assert [col_index(c) for c in ("A", "H", "Z", "AA", "AZ")] == [0, 7, 25, 26, 51]
    assert [col_letters(i) for i in (0, 7, 25, 26, 51)] == ["A", "H", "Z", "AA", "AZ"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Contacts!A2:D", A1Range("Contacts", 0, 2, 3, None)),
        ("Results!A:H", A1Range("Results", 0, None, 7, None)),
        ("Results!A5:H5", A1Range("Results", 0, 5, 7, 5)),
        ("'My Tab'!B3", A1Range("My Tab", 1, 3, 1, 3)),
        ("'It''s'!A1:B2", A1Range("It's", 0, 1, 1, 2)),
        ("Sheet1", A1Range("Sheet1", 0, None, None, None)),
    ],
)
def test_parse_range(text: str, expected: A1Range) -> None:
    assert parse_range(text) == expected


@pytest.mark.parametrize("text", ["", "!A1", "Tab!", "Tab!1A", "Tab!A1:B2:C3", "Tab!a1"])
def test_parse_range_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        parse_range(text)


def test_format_range_quotes_when_needed() -> None:
    assert format_range("Results", 0, 5, 7, 5) == "Results!A5:H5"
    assert format_range("My Tab", 1, 3, 1, 3) == "'My Tab'!B3"
