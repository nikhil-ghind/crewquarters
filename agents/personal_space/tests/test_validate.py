from crewquarters.knowledge import Passage
from personal_space.models import HighlightDraft, SpaceBrief, ThemeDraft
from personal_space.validate import (
    cited_ids,
    fallback_themes,
    resolve,
    sources_for,
    validate_brief,
)


def passage(cid: str, score: float = 0.5, doc: str = "a.md", text: str = "some text") -> Passage:
    return Passage(cid, text, score, doc.removesuffix(".md"), doc, {"section": "S"})


REFS = {"p1": passage("kb:1"), "p2": passage("kb:2", doc="b.md")}


def test_unknown_refs_are_dropped_and_real_ids_returned_in_order() -> None:
    assert resolve(["p2", "p99", " p1 ", "p2"], REFS) == ["kb:2", "kb:1"]
    assert resolve(["kb:1"], REFS) == []  # only the refs the model was shown count


def brief(**overrides: object) -> SpaceBrief:
    data: dict[str, object] = {
        "domain": "Notes",
        "summary": "s",
        "themes": [ThemeDraft(title="T", summary="ts", citations=["p1"])],
        "highlights": [
            HighlightDraft(title="H", why_it_matters="w", next_step=" do it ", citations=["p2"])
        ],
        "explore_next": ["  what next?  ", "  "],
    }
    return SpaceBrief.model_validate(data | overrides)


def test_a_cited_brief_is_kept_with_real_citation_ids() -> None:
    themes, highlights, explore = validate_brief(brief(), REFS)
    assert [(t.title, t.citations) for t in themes] == [("T", ["kb:1"])]
    assert [(h.title, h.next_step, h.citations) for h in highlights] == [("H", "do it", ["kb:2"])]
    assert explore == ["what next?"]


def test_items_without_a_valid_citation_are_dropped() -> None:
    made_up = brief(
        themes=[ThemeDraft(title="T", summary="ts", citations=["p99"])],
        highlights=[HighlightDraft(title="H", why_it_matters="w", citations=[])],
    )
    themes, highlights, _ = validate_brief(made_up, REFS)
    assert themes == [] and highlights == []


def test_lists_are_capped() -> None:
    many = brief(
        themes=[ThemeDraft(title=f"T{i}", summary="s", citations=["p1"]) for i in range(9)],
        highlights=[
            HighlightDraft(title=f"H{i}", why_it_matters="w", citations=["p1"]) for i in range(9)
        ],
        explore_next=[f"q{i}" for i in range(9)],
    )
    themes, highlights, explore = validate_brief(many, REFS)
    assert (len(themes), len(highlights), len(explore)) == (5, 6, 4)


def test_sources_cover_exactly_what_was_cited() -> None:
    themes, highlights, _ = validate_brief(brief(), REFS)
    everything = {"kb:1": REFS["p1"], "kb:2": REFS["p2"], "kb:3": passage("kb:3")}
    ids = cited_ids(themes, highlights)
    assert ids == ["kb:2", "kb:1"]  # highlights first
    sources = sources_for(ids, everything)
    assert [s.citation_id for s in sources] == ["kb:2", "kb:1"]
    assert sources[0].document_name == "b.md" and sources[0].locator == {"section": "S"}


def test_fallback_shows_the_best_passage_of_each_document_verbatim() -> None:
    found = [
        passage("a1", 0.9, "a.md", "first   passage\nof a"),
        passage("a2", 0.8, "a.md", "second"),
        passage("b1", 0.7, "b.md", "b" * 500),
    ]
    themes = fallback_themes(found)
    assert [t.title for t in themes] == ["a.md", "b.md"]
    assert themes[0].summary == "first passage of a"
    assert themes[0].citations == ["a1", "a2"]
    assert len(themes[1].summary) == 300
