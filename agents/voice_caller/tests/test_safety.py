# Ported from truestar-voice (TrueStar) at 4c34cce: tests/test_safety.py.
from voice_caller.safety import (
    graceful_recovery_message,
    is_content_filter_error,
    sanitize_user_text,
)


class TestSanitize:
    def test_clean_text_unchanged(self):
        text, modified = sanitize_user_text("How are family offices structured today?")
        assert text == "How are family offices structured today?"
        assert modified is False

    def test_profanity_masked(self):
        text, modified = sanitize_user_text("This is bullshit honestly")
        assert "*" in text
        assert "shit" not in text.lower()
        assert modified is True

    def test_multiple_profanities(self):
        text, modified = sanitize_user_text("fuck this shit")
        assert "fuck" not in text.lower()
        assert "shit" not in text.lower()
        assert modified is True

    def test_empty_string(self):
        text, modified = sanitize_user_text("")
        assert text == ""
        assert modified is False

    def test_preserves_word_boundaries(self):
        text, modified = sanitize_user_text("The assessment was thorough.")
        assert text == "The assessment was thorough."
        assert modified is False


class TestContentFilterDetection:
    def test_detects_content_filter_in_message(self):
        exc = Exception("Error code: 400 - {'error': {'code': 'content_filter'}}")
        assert is_content_filter_error(exc) is True

    def test_detects_responsibleai_in_message(self):
        exc = Exception("ResponsibleAIPolicyViolation occurred")
        assert is_content_filter_error(exc) is True

    def test_unrelated_error(self):
        exc = ValueError("bad json")
        assert is_content_filter_error(exc) is False

    def test_walks_cause_chain(self):
        inner = Exception("content_filter triggered")
        outer = Exception("wrapper")
        outer.__cause__ = inner
        assert is_content_filter_error(outer) is True


class TestRecoveryMessage:
    def test_returns_string(self):
        msg = graceful_recovery_message(0)
        assert isinstance(msg, str)
        assert len(msg) > 10

    def test_rotates_phrases(self):
        msgs = {graceful_recovery_message(i) for i in range(6)}
        assert len(msgs) >= 2


class TestSanitizeFalsePositives:
    """2026-09-22 review: the list masked a common first name and words
    that never trip Azure's filter."""

    def test_a_first_name_is_not_profanity(self):
        text, modified = sanitize_user_text("I worked with Dick on the rollout.")
        assert text == "I worked with Dick on the rollout."
        assert modified is False

    def test_harmless_words_are_left_alone(self):
        for s in ("that's a damn shame", "the numbers were crap", "he was a jerk about it"):
            text, modified = sanitize_user_text(s)
            assert text == s
            assert modified is False

    def test_anchoring_keeps_ordinary_words_intact(self):
        for s in ("we ordered shiitake", "a mishit on the launch", "they snigger at it"):
            text, modified = sanitize_user_text(s)
            assert text == s, s
            assert modified is False

    def test_compounds_still_mask(self):
        text, modified = sanitize_user_text("total bullshit and motherfucking chaos")
        assert "bullshit" not in text.lower()
        assert "fuck" not in text.lower()
        assert modified is True


class TestExceptionChainIsBounded:
    def test_a_cycle_terminates(self):
        from voice_caller.safety import _exception_chain

        a = Exception("a")
        b = Exception("b")
        a.__cause__ = b
        b.__cause__ = a
        assert len(list(_exception_chain(a))) == 2

    def test_a_long_chain_is_cut_at_the_depth_limit(self):
        from voice_caller.safety import _MAX_CHAIN_DEPTH, _exception_chain

        head = cur = Exception("0")
        for i in range(1, 50):
            nxt = Exception(str(i))
            cur.__cause__ = nxt
            cur = nxt
        assert len(list(_exception_chain(head))) == _MAX_CHAIN_DEPTH


class TestRecoveryMovesOn:
    def test_no_recovery_line_asks_them_to_repeat(self):
        """Asking them to repeat invites the exact text that tripped the
        filter, which fails again and loops."""
        from voice_caller.safety import _RECOVERY_PHRASES

        for phrase in _RECOVERY_PHRASES:
            low = phrase.lower()
            assert "repeat" not in low and "say that again" not in low and "main point" not in low
            assert "[" not in phrase and "<" not in phrase
