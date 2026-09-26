# Ported from truestar-voice (TrueStar) at 4c34cce: tests/test_texture.py.
"""The texture pass: the sound and the opener the model keeps forgetting."""

import asyncio

from voice_caller.texture import TextureState, apply_texture, humanize, strip_markup

# Crewquarters: the Inworld greeting directions lived in truestar's prompts module; the texture
# pass still protects any greeting that starts with them, so the test keeps its own copy.
CAMPAIGN_GREETING_DIRECTIONS = (
    "[warm and lively with natural rising and falling pitch like real conversation, a little quick]",
    "[friendly and playful with an easy conversational lilt, rising on the question]",
    "[bright and warm, expressive intonation with a smile in the voice, a touch quick]",
)


def run(chunks: list[str], state: TextureState | None = None) -> str:
    async def gen():
        for c in chunks:
            yield c

    async def collect():
        out = []
        async for piece in humanize(
            gen(), state or TextureState(), protected_prefixes=CAMPAIGN_GREETING_DIRECTIONS
        ):
            out.append(piece)
        return "".join(out)

    return asyncio.run(collect())


class TestSound:
    def test_a_two_sentence_turn_without_a_sound_gets_a_breath_after_the_first(self):
        out = run(
            [
                "[thoughtful and measured] Right, thin ",
                "data makes sense. What do ",
                "people avoid there?",
            ]
        )
        assert (
            out
            == "[thoughtful and measured] Right, thin data makes sense. [breathe] What do people avoid there?"
        )

    def test_a_turn_that_already_has_a_sound_is_left_alone(self):
        text = "[thoughtful] Right, [sigh] thin data. What do people avoid there?"
        assert run([text]) == text

    def test_a_single_sentence_turn_never_gets_a_sound(self):
        assert run(["Take your time."]) == "Take your time."
        assert run(["[gentle and soft] Take your time."]) == "[gentle and soft] Take your time."

    def test_breaths_rotate_between_turns(self):
        state = TextureState()
        a = run(["Right. What next?"], state)
        b = run(["Okay. Which one?"], state)
        assert "[breathe]" in a and "[tongue click]" in b

    def test_a_period_inside_a_direction_is_not_a_sentence_end(self):
        out = run(["[say it slowly. gently] Makes sense, what changed?"])
        assert "[breathe]" not in out


class TestOpener:
    def test_a_turn_that_opens_on_a_question_word_gets_a_spoken_opener(self):
        out = run(["[genuinely curious] What did your team actually do?"])
        assert out == "[genuinely curious] So, what did your team actually do?"

    def test_a_turn_that_already_reacts_first_is_left_alone(self):
        text = "[warm] Right, that makes sense. Why's that?"
        assert run([text]).replace(" [breathe]", "") == text

    def test_openers_rotate(self):
        state = TextureState()
        a = run(["Which one?"], state)
        b = run(["How so?"], state)
        assert a.startswith("So, ") and b.startswith("Well, ")


class TestProtectedLines:
    def test_the_greeting_is_never_touched(self):
        for d in CAMPAIGN_GREETING_DIRECTIONS:
            g = f"{d} Hey Dana? It's Aria. Sorry, total cold call. Got ten minutes?"
            assert run([g]) == g

    def test_goodbyes_and_rejections_are_never_touched(self):
        for line in (
            "No problem at all. Thanks for your time.",
            "Of course. Thanks, and have a good one.",
            "[lower and calmer] Understood, no problem. Take care.",
        ):
            assert run([line]) == line


class TestRepairs:
    def test_a_sound_merged_into_a_direction_bracket_is_split(self):
        out = apply_texture(
            "[clear throat, warm and lively] Thanks Sayeed — which platforms?",
            TextureState(),
            has_more_sentences=False,
        )
        assert out.startswith("[clear throat] [warm and lively] ")

    def test_empty_stream_passes_through(self):
        assert run([""]) == ""


class TestBracketOnlyTurnsNeverReachTheVoice:
    def test_a_direction_followed_by_a_newline_stays_glued_to_its_words(self):
        out = run(
            ["[warm and lively, a little quick]\n", "Hi, Derek? It's Aria. ", "Got ten minutes?"]
        )
        assert "]\n" not in out and out.startswith("[warm and lively, a little quick] Hi, Derek?")

    def test_a_bare_direction_is_dropped_entirely(self):
        assert run(["[warm and lively, natural rising and falling pitch, a little quick]"]) == ""
        assert run(["[sigh]"]) == ""

    def test_blank_lines_mid_turn_are_collapsed(self):
        out = run(["Right, got it.\n\nWhat changed?"])
        assert "\n\n" not in out


class TestPlainVoice:
    """Crewquarters: the local voice (Kokoro) performs no bracket tags."""

    def test_sounds_are_not_injected_for_a_plain_voice(self):
        async def collect() -> str:
            async def gen():
                yield "Right. What do people avoid there?"

            out = [p async for p in humanize(gen(), TextureState(), sounds=False)]
            return "".join(out)

        assert asyncio.run(collect()) == "Right. What do people avoid there?"

    def test_openers_still_apply_for_a_plain_voice(self):
        out = apply_texture(
            "What made you pick that one?", TextureState(), has_more_sentences=False, sounds=False
        )
        assert out == "So, what made you pick that one?"

    def test_strip_markup_removes_everything_the_voice_would_read_aloud(self):
        text = '[warm and lively] Hi, [breathe] it is <break time="300ms" /> Sam. <verbatim>KT7</verbatim>'
        assert strip_markup(text) == "Hi, it is Sam. KT7"
        assert strip_markup("[sigh]") == ""
