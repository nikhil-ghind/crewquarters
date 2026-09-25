from pathlib import Path

from crewquarters_fake.knowledge import KnowledgeIndex


def build(tmp_path: Path) -> KnowledgeIndex:
    (tmp_path / "terms.md").write_text(
        "# Cancellation\n\nCustomers may cancel within 30 days for a full refund.\n\n"
        "# Delivery\n\nOrders ship within two business days.\n"
    )
    (tmp_path / "faq.txt").write_text("Support hours are 9am to 5pm IST on weekdays.\n")
    (tmp_path / "ignored.pdf").write_bytes(b"%PDF")
    index = KnowledgeIndex()
    index.load_dir("kb-1", tmp_path)
    return index


def test_search_ranks_the_matching_passage_first(tmp_path: Path) -> None:
    passages = build(tmp_path).search("kb-1", "How do I cancel for a refund?", top_k=3)
    assert passages[0]["document"] == {"id": "terms", "name": "terms.md"}
    assert "cancel" in passages[0]["text"].lower()
    assert passages[0]["locator"] == {"section": "Cancellation"}
    assert passages[0]["citationId"].startswith("kb:kb-1:doc:terms:chunk:")
    assert passages[0]["score"] > 0


def test_search_filters_documents_and_skips_zero_scores(tmp_path: Path) -> None:
    index = build(tmp_path)
    assert [
        p["document"]["id"] for p in index.search("kb-1", "support hours", document_ids=["faq"])
    ] == ["faq"]
    assert index.search("kb-1", "support hours", document_ids=["terms"]) == []
    assert index.search("kb-1", "zebra quantum") == []


def test_has_kb(tmp_path: Path) -> None:
    index = build(tmp_path)
    assert index.has("kb-1")
    assert not index.has("kb-2")
