"""Prompts. Retrieved passages only ever appear inside evidence blocks in the user message."""

from __future__ import annotations

from datetime import date

from crewquarters.untrusted import GUARD_INSTRUCTIONS, evidence

EXPAND_SYSTEM = f"""You help explore an owner's private knowledge base without seeing all of it.

{GUARD_INSTRUCTIONS}

From the evidence sample, propose short search queries (2 to 8 words each) that would surface other
parts of the same knowledge base: distinct topics, projects, people, goals, or open items that the
sample hints at but does not cover. Do not repeat queries you were told were already used.
Reply only with JSON that matches the requested schema."""

SYNTH_SYSTEM = f"""You build a personalized brief from an owner's own knowledge base.

{GUARD_INSTRUCTIONS}

First work out what kind of collection this is (for example personal notes, a research library, a
recipe collection, or product manuals) and put that in domain. Then write:
- summary: two or three sentences on what the collection holds and what stands out for this owner.
- themes: two to five main themes, each with a short summary.
- highlights: up to six items that matter most for this owner, each with why it matters and, only
  when the evidence supports one, a concrete next step. Rank by what the owner asked for when an
  intent is given; otherwise by what the documents say about the owner's own goals, priorities,
  projects, deadlines, or open items.
- exploreNext: up to four short questions the owner could ask their knowledge base next.

Personalize only from the owner's stated intent and from what the documents say. Never guess
personal details, and never invent dates, names, numbers, or facts that are not in the evidence.
Every theme and highlight must cite the refs (like p1, p3) of the evidence blocks that support it,
and must be left out when no evidence supports it. Reply only with JSON that matches the requested
schema."""

REPAIR = (
    "Your previous reply did not match the required JSON schema. Reply again with only JSON that "
    "matches the schema; every theme and highlight needs citations that are refs of the evidence "
    "blocks."
)


def _blocks(passages: dict[str, tuple[str, str]], boundary: str) -> str:
    """``passages`` maps a ref to (source label, text)."""
    return "\n\n".join(
        evidence(text, ref=ref, source=source, boundary=boundary)
        for ref, (source, text) in passages.items()
    )


def expand_prompt(
    passages: dict[str, tuple[str, str]],
    boundary: str,
    used: list[str],
    intent: str | None,
    limit: int,
) -> str:
    focus = f"The owner's stated intent: {intent}\n" if intent else ""
    tried = "; ".join(used) if used else "none"
    return (
        f"{focus}Queries already used: {tried}\n"
        f"Propose up to {limit} new queries.\n\n" + _blocks(passages, boundary)
    )


def synth_prompt(
    passages: dict[str, tuple[str, str]], boundary: str, intent: str | None, today: date
) -> str:
    focus = (
        f"The owner's stated intent: {intent}" if intent else "The owner gave no specific intent."
    )
    return (
        f"Today is {today.isoformat()}. {focus}\n"
        f"Build the brief from these {len(passages)} evidence blocks "
        f"(refs {', '.join(passages)}).\n\n" + _blocks(passages, boundary)
    )
