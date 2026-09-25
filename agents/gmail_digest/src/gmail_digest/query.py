"""Gmail search query for a UTC window."""

from __future__ import annotations

from datetime import datetime

CATEGORY_QUERY_NAMES = {
    "CATEGORY_PROMOTIONS": "promotions",
    "CATEGORY_SOCIAL": "social",
    "CATEGORY_UPDATES": "updates",
    "CATEGORY_FORUMS": "forums",
}


def build_query(
    start: datetime, end: datetime, exclude_categories: list[str], include_labels: list[str]
) -> str:
    terms = [f"after:{int(start.timestamp())}", f"before:{int(end.timestamp())}"]
    terms += [f"-category:{CATEGORY_QUERY_NAMES[c]}" for c in exclude_categories]
    terms += [f"label:{label}" for label in include_labels]
    return " ".join(terms)
