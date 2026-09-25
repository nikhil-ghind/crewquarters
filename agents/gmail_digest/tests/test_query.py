from datetime import UTC, datetime

from gmail_digest.query import build_query


def test_build_query_exact_string() -> None:
    start = datetime(2026, 9, 22, 18, 30, tzinfo=UTC)
    end = datetime(2026, 9, 23, 18, 30, tzinfo=UTC)
    query = build_query(start, end, ["CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"], ["Work"])
    assert (
        query
        == "after:1790101800 before:1790188200 -category:promotions -category:social label:Work"
    )


def test_build_query_without_filters() -> None:
    start = datetime(2026, 9, 22, 18, 30, tzinfo=UTC)
    end = datetime(2026, 9, 23, 18, 30, tzinfo=UTC)
    assert build_query(start, end, [], []) == "after:1790101800 before:1790188200"
