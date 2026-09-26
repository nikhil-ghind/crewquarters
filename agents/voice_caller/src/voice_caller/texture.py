"""Guarantee the human texture the model keeps forgetting.

Measured on the live prompt with gpt-5.4-mini (2026-09-23, two simulated
seven-turn calls): delivery directions on 5-6 turns of 7, but non-verbal
sounds on only 1-3, spoken fillers on 3-4, and turns averaging eleven
words. "One sound on every turn" in the prompt, with a pre-send checklist,
moved sounds from 0 to 3 of 7 — still not the texture the owner hears as
a person. So the last mile is done here, deterministically, on the text
on its way to the voice:

  1. A turn with two or more sentences and no sound gets one breath-class
     sound ([breathe] / [tongue click] / [lip smack], rotating) placed
     after its first sentence — the natural spot, between the reaction
     and the question.
  2. A turn that opens straight on a question word gets a spoken opener
     ("So, " / "Well, " / "Okay, so ", rotating) — the difference between
     a read question and an asked one.
  3. "[clear throat, warm and lively]" — a sound the model merged into
     the direction bracket, which the engine performs as neither — is
     split into "[clear throat] [warm and lively]".

Anything the model wrote itself takes precedence: a turn that already has
a sound or an opener is left alone. The greeting (its delivery is tuned
and measured separately), goodbyes and rejections are never touched, and
a single-sentence turn never gets a sound — "Take your time." stays
exactly that.

The pass sees the text stream the LLM produces, holds it only until the
first sentence boundary (a few tokens), and passes everything after that
straight through, so it costs nothing the caller could hear.
"""
# Ported from truestar-voice (TrueStar) at 4c34cce: src/texture.py.
# Changes for Crewquarters are marked "Crewquarters:".

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator

SOUND_NAMES: tuple[str, ...] = (
    "laugh",
    "chuckle",
    "breathe",
    "sigh",
    "clear throat",
    "tongue click",
    "lip smack",
    "sniff",
    "swallow",
    "huff",
    "scoff",
    "gasp",
    "snort",
    "giggle",
    "cough",
    "yawn",
)
# Subtle ones only — a laugh or a sigh injected blind would be wrong more
# often than right; a breath between two sentences never is.
INJECTED_SOUNDS: tuple[str, ...] = ("breathe", "tongue click", "lip smack")
OPENERS: tuple[str, ...] = ("So, ", "Well, ", "Okay, so ")

_QUESTION_WORDS = {
    "what",
    "which",
    "how",
    "when",
    "why",
    "where",
    "who",
    "is",
    "are",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "was",
    "were",
    "have",
    "has",
    "any",
    "anything",
}
# Turns that must not acquire a sound: they are exits, and the prompt's own
# rule is "none at all on a rejection or a goodbye".
_EXIT_RE = re.compile(
    r"\b(thanks|thank you|take care|have a good|no problem|appreciate|bye|goodbye|good one)\b", re.I
)
_LEADING_BRACKETS_RE = re.compile(r"^(\s*(?:\[[^\]]*\]\s*)*)")
_SENTENCE_END_RE = re.compile(r"[.!?…][\"')\]]?\s")
_SOUND_ALT = "|".join(re.escape(s) for s in SOUND_NAMES)
_MERGED_SOUND_RE = re.compile(rf"\[\s*({_SOUND_ALT})\s*,\s*([^\]]+)\]", re.I)
_ANY_SOUND_RE = re.compile(rf"\[\s*(?:{_SOUND_ALT})\s*\]", re.I)

# Give up waiting for a sentence boundary past this many characters: a
# long first sentence is still spoken promptly, just without the injected
# beat.
_MAX_HOLD_CHARS = 160


class TextureState:
    """Per-agent rotation counters, so consecutive turns do not repeat the
    same breath or the same opener."""

    def __init__(self) -> None:
        self.sound_i = 0
        self.opener_i = 0

    def next_sound(self) -> str:
        s = INJECTED_SOUNDS[self.sound_i % len(INJECTED_SOUNDS)]
        self.sound_i += 1
        return s

    def next_opener(self) -> str:
        o = OPENERS[self.opener_i % len(OPENERS)]
        self.opener_i += 1
        return o


_SPOKEN_WORD_RE = re.compile(r"[A-Za-z0-9\u00C0-\u024F\u0400-\u04FF\u0900-\u0D7F]")


def has_spoken_words(text: str) -> bool:
    """True when something would actually be voiced once brackets and
    tags are removed."""
    return bool(_SPOKEN_WORD_RE.search(re.sub(r"\[[^\]]*\]|<[^>]+>", " ", text)))


def glue_directions(text: str) -> str:
    """A line break right after a direction bracket becomes a space.

    On call adefc726 (2026-09-23) the model wrote the direction, a newline,
    then its words; the sentence splitter sent the bracket to the voice
    as a segment of its own, Inworld returned no audio for it, the whole
    turn errored out and Aria said nothing for the rest of the call.
    The prompt forbids blank lines, but the pipeline must not depend on
    the model obeying that."""
    text = re.sub(r"\]\s*\n+\s*", "] ", text)
    return re.sub(r"\n{2,}", " ", text)


def _masked(text: str) -> str:
    """Same length as `text`, bracket contents replaced, so indices map 1:1
    and a period inside a direction is not mistaken for a sentence end."""
    return re.sub(r"\[[^\]]*\]", lambda m: "x" * len(m.group(0)), text)


def apply_texture(
    head: str,
    state: TextureState,
    *,
    has_more_sentences: bool,
    protected_prefixes: tuple[str, ...] = (),
    sounds: bool = True,
) -> str:
    """Transform the opening of one turn. `head` runs at least through the
    first sentence boundary when `has_more_sentences` is True."""
    if not head.strip():
        return head

    head = glue_directions(head)

    # 3. A sound merged into a direction bracket performs as neither.
    head = _MERGED_SOUND_RE.sub(lambda m: f"[{m.group(1).lower()}] [{m.group(2).strip()}]", head)

    stripped = head.lstrip()
    if any(stripped.startswith(p) for p in protected_prefixes):
        return head
    if _EXIT_RE.search(_masked(head)):
        return head

    # 1. One breath-class sound after the first sentence when the model
    #    wrote none and there is a second sentence to breathe before.
    # Crewquarters: a voice that performs no bracket tags (Kokoro) gets no injected sounds.
    if sounds and has_more_sentences and not _ANY_SOUND_RE.search(head):
        m = _SENTENCE_END_RE.search(_masked(head))
        if m:
            cut = m.end() - 1  # just before the whitespace that follows the punctuation
            head = f"{head[:cut]} [{state.next_sound()}]{head[cut:]}"

    # 2. A spoken opener when the turn starts straight on a question word.
    lead = _LEADING_BRACKETS_RE.match(head)
    prefix_len = lead.end() if lead else 0
    body = head[prefix_len:]
    first = re.match(r"\s*([A-Za-z']+)", body)
    if first and first.group(1).lower() in _QUESTION_WORDS:
        rest = body.lstrip()
        head = f"{head[:prefix_len]}{state.next_opener()}{rest[0].lower()}{rest[1:]}"

    return head


async def humanize(
    text: AsyncIterable[str],
    state: TextureState,
    *,
    protected_prefixes: tuple[str, ...] = (),
    sounds: bool = True,
) -> AsyncIterator[str]:
    """Wrap an LLM text stream: hold the head until the first sentence
    boundary (or a size cap), transform it once, pass the rest through."""
    head = ""
    decided = False
    async for chunk in text:
        if decided:
            yield glue_directions(chunk)
            continue
        head += chunk
        m = _SENTENCE_END_RE.search(_masked(head))
        if m and len(head) > m.end():
            yield apply_texture(
                head,
                state,
                has_more_sentences=True,
                protected_prefixes=protected_prefixes,
                sounds=sounds,
            )
            decided = True
        elif len(head) >= _MAX_HOLD_CHARS:
            yield apply_texture(
                head,
                state,
                has_more_sentences=False,
                protected_prefixes=protected_prefixes,
                sounds=sounds,
            )
            decided = True
    if not decided:
        # Single sentence, or the stream ended inside the first one: an
        # opener may still apply, a sound never does. A turn with nothing
        # to voice (a bare direction) is dropped here rather than sent to
        # the engine, which answers it with an error and no audio.
        if not has_spoken_words(head):
            return
        yield apply_texture(
            head,
            state,
            has_more_sentences=False,
            protected_prefixes=protected_prefixes,
            sounds=sounds,
        )


def strip_markup(text: str) -> str:
    """Remove delivery notation (brackets, SSML-style tags) so only speech remains.

    Crewquarters: ported from truestar-voice's ``campaign_agent._strip_markup``. The local voice
    reads brackets aloud, so this runs on everything sent to it and on the stored transcript."""
    if not text:
        return text
    cleaned = re.sub(r"<\s*verbatim\s*>(.*?)<\s*/\s*verbatim\s*>", r"\1", text, flags=re.I | re.S)
    cleaned = re.sub(r"<\s*break\b[^>]*/?\s*>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\[[^\]\n]{1,240}\]", " ", cleaned)
    cleaned = re.sub(r"<\s*expr\b[^>]*/?\s*>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    return cleaned.strip()
