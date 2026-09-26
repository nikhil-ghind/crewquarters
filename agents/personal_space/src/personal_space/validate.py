"""Turn model output into a result. Code, not the model, decides what counts as cited."""

from __future__ import annotations

from collections.abc import Iterable

from crewquarters.knowledge import Passage
from personal_space.models import (
    Highlight,
    SourceRef,
    SpaceBrief,
    Theme,
)

MAX_THEMES = 5
MAX_HIGHLIGHTS = 6
MAX_EXPLORE = 4
MAX_FALLBACK_DOCUMENTS = 6


def resolve(citations: Iterable[str], refs: dict[str, Passage]) -> list[str]:
    """Map the model's refs to real citation ids; unknown refs are dropped, order is kept."""
    seen: list[str] = []
    for ref in citations:
        passage = refs.get(ref.strip())
        if passage is not None and passage.citation_id not in seen:
            seen.append(passage.citation_id)
    return seen


def validate_brief(
    brief: SpaceBrief, refs: dict[str, Passage]
) -> tuple[list[Theme], list[Highlight], list[str]]:
    """Keep only themes and highlights that cite at least one passage the model was shown."""
    themes = [
        Theme(title=t.title.strip(), summary=t.summary.strip(), citations=cited)
        for t in brief.themes
        if (cited := resolve(t.citations, refs)) and t.title.strip()
    ][:MAX_THEMES]
    highlights = [
        Highlight(
            title=h.title.strip(),
            why_it_matters=h.why_it_matters.strip(),
            next_step=(h.next_step or "").strip() or None,
            citations=cited,
        )
        for h in brief.highlights
        if (cited := resolve(h.citations, refs)) and h.title.strip()
    ][:MAX_HIGHLIGHTS]
    explore = [q.strip() for q in brief.explore_next if q.strip()][:MAX_EXPLORE]
    return themes, highlights, explore


def sources_for(cited: Iterable[str], passages: dict[str, Passage]) -> list[SourceRef]:
    """One source per cited passage, in order of first citation."""
    ordered: list[str] = []
    for citation_id in cited:
        if citation_id in passages and citation_id not in ordered:
            ordered.append(citation_id)
    return [
        SourceRef(
            citation_id=cid,
            document_name=passages[cid].document_name,
            locator=dict(passages[cid].locator),
            score=round(passages[cid].score, 4),
        )
        for cid in ordered
    ]


def fallback_themes(passages: Iterable[Passage]) -> list[Theme]:
    """No model prose: one theme per document, showing its best passage verbatim."""
    best: dict[str, list[Passage]] = {}
    for passage in sorted(passages, key=lambda p: p.score, reverse=True):
        best.setdefault(passage.document_id, []).append(passage)
    themes = []
    for group in list(best.values())[:MAX_FALLBACK_DOCUMENTS]:
        excerpt = " ".join(group[0].text.split())
        themes.append(
            Theme(
                title=group[0].document_name,
                summary=excerpt[:300],
                citations=[p.citation_id for p in group[:3]],
            )
        )
    return themes


def cited_ids(themes: list[Theme], highlights: list[Highlight]) -> list[str]:
    ids: list[str] = []
    for highlight in highlights:
        ids.extend(highlight.citations)
    for theme in themes:
        ids.extend(theme.citations)
    return ids
