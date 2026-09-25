"""Chunk size, overlap, boundary snapping, locators, and citation text."""

from __future__ import annotations

import itertools

import pytest
from crewquarters_knowledge.chunking import TOKEN, chunk, count_tokens, describe
from crewquarters_knowledge.extract import Segment

pytestmark = pytest.mark.no_db


def _words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_short_document_is_one_chunk() -> None:
    [only] = chunk([Segment("Hello, world.", {"page": 1})], 800, 120)
    assert only.text == "Hello, world." and only.token_count == 4
    assert only.locator == {"page": 1}


def test_window_size_and_overlap() -> None:
    chunks = chunk([Segment(_words(250), {"line": 1})], 100, 20)
    assert [c.token_count for c in chunks] == [100, 100, 90]
    for previous, current in itertools.pairwise(chunks):
        tail = TOKEN.findall(previous.text)[-20:]
        assert TOKEN.findall(current.text)[:20] == tail


def test_prefers_to_end_at_a_segment_boundary() -> None:
    segments = [Segment(_words(85, "a"), {"page": 1}), Segment(_words(60, "b"), {"page": 2})]
    first, second = chunk(segments, 100, 10)
    assert first.token_count == 85 and first.locator == {"page": 1}
    assert second.text.startswith("a75")
    assert second.locator == {"page": 1, "through": {"page": 2}}


def test_every_token_is_covered_once_ignoring_overlap() -> None:
    text = _words(1000)
    chunks = chunk([Segment(text, {})], 800, 120)
    assert len(chunks) == 2 and chunks[0].token_count == 800
    assert chunks[1].text.endswith("w999") and count_tokens(text) == 1000


def test_empty_and_invalid() -> None:
    assert chunk([], 800, 120) == []
    with pytest.raises(ValueError, match="overlap"):
        chunk([Segment("x", {})], 10, 10)


@pytest.mark.parametrize(
    ("locator", "text"),
    [
        ({"page": 3}, "page 3"),
        ({"page": 3, "through": {"page": 4}}, "page 3-4"),
        ({"row": 2, "through": {"row": 40}}, "row 2-40"),
        ({"section": "Refunds", "line": 3}, "Refunds (line 3)"),
        ({"table": 1, "row": 2}, "table 1, row 2"),
        ({"paragraph": 2, "through": {"table": 1, "row": 1}}, "paragraph 2 to table 1, row 1"),
        ({"section": "Only"}, "Only"),
        ({}, ""),
    ],
)
def test_describe(locator: dict[str, object], text: str) -> None:
    assert describe(locator) == text
