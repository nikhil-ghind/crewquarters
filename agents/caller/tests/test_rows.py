from caller_agent.rows import ContactRow, classify, read_rows


def rows(*values: list[str], start: int = 2) -> list[ContactRow]:
    return read_rows([list(v) for v in values], start)


def reasons(plan_skipped: list) -> dict[int, str]:  # type: ignore[type-arg]
    return {s.row: s.reason for s in plan_skipped}


def test_read_rows_pads_missing_cells_trims_and_skips_empty_rows() -> None:
    contacts = rows(
        ["Asha", " +15555550101 ", "yes"],
        [],
        ["", "", "", ""],
        ["Ben", "+15555550102", "no", "done"],
    )
    assert contacts == [
        ContactRow(2, "Asha", "+15555550101", "yes", ""),
        ContactRow(5, "Ben", "+15555550102", "no", "done"),
    ]


def test_every_skip_rule() -> None:
    plan = classify(
        rows(
            ["Asha", "+15555550101", "yes", ""],
            ["Ben", "+15555550102", "no", ""],
            ["Chen", "+15555550103", "YES", "ready"],
            ["Dana", "5550104", "yes", ""],
            ["Eve", "+15555550105", "consented", "Pending"],
            ["Fay", "+15555550106", "yes", "done"],
            ["Gus", "+15555550101", "true", ""],
            ["Hal", "+15555550107", "yes", "maybe later"],
            ["Ivy", "+15555550108", "yes", ""],
            ["Jo", "+15555550109", "", ""],
            ["Kim", "+1 555 555 0110", "yes", ""],
            ["Lee", "+15555550111", "yes", "DNC"],
        ),
        max_calls=3,
    )
    assert [c.row for c in plan.eligible] == [2, 4, 6]
    assert reasons(plan.skipped) == {
        3: "consent",
        5: "invalid_number",
        7: "status",
        8: "duplicate",
        9: "unrecognized_status",
        10: "over_cap",
        11: "consent",
        12: "invalid_number",
        13: "status",
    }


def test_header_row_is_skipped_not_called() -> None:
    plan = classify(
        rows(
            ["name", "phone_e164", "consent", "status"],
            ["Asha", "+15555550101", "yes", ""],
            start=1,
        ),
        3,
    )
    assert [c.row for c in plan.eligible] == [2]
    assert reasons(plan.skipped) == {1: "consent"}


def test_consent_values_are_strict() -> None:
    plan = classify(
        rows(
            ["A", "+15555550101", "y", ""],
            ["B", "+15555550102", "ok", ""],
            ["C", "+15555550103", " True ", ""],
        ),
        3,
    )
    assert [c.name for c in plan.eligible] == ["C"]
