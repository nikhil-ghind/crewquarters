from crewquarters.knowledge import Passage
from personal_space.discovery import (
    PER_PASSAGE_CHARS,
    PROBES,
    Retrieved,
    clean_query,
    derive_queries,
    probe_queries,
    prompt_refs,
    select_for_prompt,
)


def passage(
    cid: str, score: float, doc: str = "notes.md", text: str = "text", section: str | None = None
) -> Passage:
    return Passage(
        citation_id=cid,
        text=text,
        score=score,
        document_id=doc.removesuffix(".md"),
        document_name=doc,
        locator={"section": section} if section else {},
    )


def test_probes_lead_with_the_intent_when_there_is_one() -> None:
    assert probe_queries(None) == list(PROBES)
    assert probe_queries("  plan my   week ") == ["plan my week", *PROBES]


def test_queries_are_bounded_and_whitespace_collapsed() -> None:
    assert clean_query("a \n  b") == "a b"
    assert len(clean_query("x" * 1000)) == 300


def test_cutoff_drops_weak_passages_but_remembers_them() -> None:
    retrieved = Retrieved()
    retrieved.add([passage("a", 0.9), passage("b", 0.1)], min_relevance=0.3)
    assert list(retrieved.passages) == ["a"]
    assert retrieved.seen == {"a", "b"}


def test_a_passage_found_twice_keeps_its_best_score() -> None:
    retrieved = Retrieved()
    retrieved.add([passage("a", 0.4)], 0.3)
    retrieved.add([passage("a", 0.8), passage("c", 0.5)], 0.3)
    assert retrieved.passages["a"].score == 0.8
    assert len(retrieved.passages) == 2


def test_cap_keeps_the_best_passages() -> None:
    retrieved = Retrieved()
    retrieved.add([passage(str(i), i / 10) for i in range(1, 6)], 0)
    retrieved.cap(2)
    assert set(retrieved.passages) == {"5", "4"}


def test_follow_up_queries_come_from_headings_then_document_names() -> None:
    found = [
        passage("a", 0.9, "goals.md", section="Goals"),
        passage("b", 0.8, "home-lab_notes.md"),
        passage("c", 0.7, "goals.md", section="Goals"),
    ]
    assert derive_queries(found, ["already used"], 5) == ["Goals", "home lab notes"]


def test_follow_up_queries_skip_what_was_already_run_and_respect_the_limit() -> None:
    found = [passage("a", 0.9, section="Goals"), passage("b", 0.8, section="Plans")]
    assert derive_queries(found, ["goals"], 5) == ["Plans"]
    assert derive_queries(found, [], 1) == ["Goals"]


def test_prompt_budget_is_shared_across_documents() -> None:
    big = "x" * 500
    found = [passage(f"a{i}", 0.9 - i / 100, "a.md", big) for i in range(5)]
    found.append(passage("b0", 0.1, "b.md", big))
    chosen = select_for_prompt(found, max_chars=1000)
    # The weak passage of the second document is picked before the third and fourth of the first.
    assert [p.citation_id for p in chosen] == ["a0", "b0"]


def test_prompt_selection_always_includes_one_passage_and_truncates_it() -> None:
    [only] = select_for_prompt([passage("a", 0.5, text="y" * 5000)], max_chars=10)
    shown, refs = prompt_refs([only])
    assert list(shown) == ["p1"] and refs["p1"] is only
    assert len(shown["p1"][1]) == PER_PASSAGE_CHARS


def test_prompt_refs_are_short_and_labelled_with_the_section() -> None:
    shown, refs = prompt_refs(
        [passage("kb:1:doc:a:chunk:0", 0.5, "goals.md", section="Goals"), passage("z", 0.4)]
    )
    assert list(refs) == ["p1", "p2"]
    assert shown["p1"][0] == "goals.md § Goals"
    assert shown["p2"][0] == "notes.md"
