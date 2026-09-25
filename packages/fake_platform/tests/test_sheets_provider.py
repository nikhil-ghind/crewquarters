import pytest

from crewquarters_fake.errors import ApiError
from crewquarters_fake.providers.sheets import SheetsProvider


def provider() -> SheetsProvider:
    sheets = SheetsProvider()
    sheets.load(
        {
            "sheet-1": {
                "Contacts": [
                    ["name", "phone_e164", "consent", "status"],
                    ["Asha", "+15555550101", "yes", ""],
                    ["Ben", "+15555550102", "no"],
                    [],
                    ["Cy", 15555550103, True, None],
                ],
                "Results": [],
            }
        }
    )
    return sheets


def test_get_reads_range_and_trims_trailing_blanks() -> None:
    values = provider().get("sheet-1", "Contacts!A2:D")
    assert values["values"] == [
        ["Asha", "+15555550101", "yes"],
        ["Ben", "+15555550102", "no"],
        [],
        ["Cy", "15555550103", "TRUE"],
    ]
    assert values["range"] == "Contacts!A2:D"


def test_get_column_subset() -> None:
    assert provider().get("sheet-1", "Contacts!B2:B3")["values"] == [
        ["+15555550101"],
        ["+15555550102"],
    ]


def test_update_writes_at_the_row_and_is_idempotent() -> None:
    sheets = provider()
    first = sheets.update("sheet-1", "Results!A5:H5", [["5", "Asha", "••••0101"]])
    second = sheets.update("sheet-1", "Results!A5:H5", [["5", "Asha", "••••0101"]])
    assert first == second == {"updatedRange": "Results!A5:C5", "updatedRows": 1}
    rows = sheets.snapshot()["sheet-1"]["Results"]
    assert rows[4] == ["5", "Asha", "••••0101"]
    assert rows[:4] == [[], [], [], []]


def test_update_without_row_starts_at_row_one() -> None:
    sheets = provider()
    sheets.update("sheet-1", "Results!A:H", [["h1", "h2"]])
    assert sheets.snapshot()["sheet-1"]["Results"][0] == ["h1", "h2"]


def test_append_goes_after_the_last_non_empty_row() -> None:
    sheets = provider()
    result = sheets.append("sheet-1", "Contacts!A:D", [["Di", "+15555550104", "yes", ""]])
    assert result == {"updatedRange": "Contacts!A6:D6", "updatedRows": 1}
    again = sheets.append("sheet-1", "Contacts!A:D", [["Ed", "+15555550105", "yes", ""]])
    assert again["updatedRange"] == "Contacts!A7:D7"


def test_missing_spreadsheet_or_tab_is_not_found() -> None:
    with pytest.raises(ApiError) as info:
        provider().get("nope", "Contacts!A1")
    assert info.value.status == 404
    with pytest.raises(ApiError):
        provider().get("sheet-1", "Missing!A1")


def test_bad_range_is_invalid_request() -> None:
    with pytest.raises(ApiError) as info:
        provider().get("sheet-1", "Contacts!??")
    assert info.value.status == 400
