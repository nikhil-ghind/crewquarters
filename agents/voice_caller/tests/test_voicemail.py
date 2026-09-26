# Ported from truestar-voice (TrueStar) at 4c34cce: tests/test_voicemail.py.
"""Voicemail and IVR detection.

Retell gave us this for free; LiveKit does not. It matters here because most
reachable numbers on a sourced brief are shared work lines that answer with
an IVR tree, and an agent that interviews a recorded greeting for ten
minutes bills us for the privilege.
"""

from voice_caller.voicemail import VoicemailDetector


def test_flags_silence_after_answer():
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    assert d.check(now=3.9) is None
    assert d.check(now=4.1) == "voicemail_reached"


def test_flags_an_unbroken_monologue_as_an_ivr():
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    d.on_remote_speech_start(at=0.5)
    assert d.check(now=6.0) is None
    assert d.check(now=6.6) == "ivr_reached"


def test_a_real_conversation_never_fires():
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    d.on_remote_speech_start(at=0.8)
    d.on_remote_speech_end(at=3.0)  # they paused — a human
    assert d.check(now=3.5) is None
    d.on_remote_speech_start(at=4.0)
    d.on_remote_speech_end(at=5.5)
    assert d.check(now=10.0) is None


def test_speech_before_the_silence_limit_clears_it():
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    d.on_remote_speech_start(at=2.0)
    d.on_remote_speech_end(at=2.4)
    assert d.check(now=9.0) is None


def test_detector_only_arms_after_answer():
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    assert d.check(now=100.0) is None


# ── C2: arming the detector relative to the greeting ────────────────────
#
# campaign_agent.py used to call detector.on_answer() BEFORE speaking the
# greeting. The greeting is ~28 words (~5-6s of speech) and the silence
# limit is 4.0s, so the detector tripped mid-greeting and hung up on any
# human who answered and then politely listened. These two tests pin the
# timeline on both sides of that fix: the same call transcript is a false
# positive when armed at answer, and correctly silent when armed at
# greeting-end, which is what the entrypoint now does.

GREETING_ENDS_AT = 5.5  # measured length of build_campaign_greeting speech


def test_polite_human_who_listens_through_the_greeting_is_not_flagged():
    """The C2 regression: answer at 0.3s, silent through the greeting, then
    speaks at 6.2s. Must NOT be flagged as voicemail."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)

    # Armed only once the greeting has finished speaking.
    d.on_answer(at=GREETING_ENDS_AT)

    # The moments that used to fire (t=4.1s, mid-greeting) are now quiet,
    # because the silence window does not start until the greeting ends.
    assert d.check(now=4.1) is None
    assert d.check(now=5.6) is None
    # Still within the post-greeting grace window.
    assert d.check(now=9.0) is None

    # They answer the question a beat later — a real conversation.
    d.on_remote_speech_start(at=6.2)
    d.on_remote_speech_end(at=8.0)
    assert d.check(now=20.0) is None


def test_arming_before_the_greeting_would_have_hung_up_on_that_same_human():
    """Documents the defect this fix removes: identical caller behaviour,
    armed at answer instead of greeting-end, fires a false voicemail."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)  # the OLD placement, before session.say(greeting)
    # The human is still listening to the greeting here, saying nothing.
    assert d.check(now=4.1) == "voicemail_reached"


def test_real_voicemail_is_still_caught_when_armed_after_the_greeting():
    """Arming later must not blind the detector to an actual voicemail box:
    dead air after the beep still trips the silence limit."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=GREETING_ENDS_AT)
    assert d.check(now=GREETING_ENDS_AT + 3.9) is None
    assert d.check(now=GREETING_ENDS_AT + 4.1) == "voicemail_reached"


def test_ivr_talking_over_the_greeting_is_still_caught():
    """An IVR tree that monologues past the greeting still reads as ivr."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=GREETING_ENDS_AT)
    d.on_remote_speech_start(at=GREETING_ENDS_AT + 0.2)
    assert d.check(now=GREETING_ENDS_AT + 5.0) is None
    assert d.check(now=GREETING_ENDS_AT + 6.5) == "ivr_reached"


# ── BUG A: the detector hung up on a live human ─────────────────────────
#
# Call f8150d92-f44e-4293-a02c-a1612dd70af0 ended after 18s as
# voicemail_reached with a consenting expert on the line. Transcript:
#
#   agent  14887ms  "Hey Sayeed, this is Aria ... ten minutes?"
#   expert 14899ms  "Yep. Yes, sure."
#   agent  18948ms  "Thanks. I noticed you were"      <- cut off mid-word
#
# The detector was wired ONLY to user_state_changed, so it had no idea
# the agent was mid-reply. Its 4s silence timer ran straight through
# Aria's answer and fired at t+4s. Two independent defects, each of which
# alone is sufficient to reproduce the hangup, so both are pinned here.


def test_a_human_who_speaks_then_listens_to_a_long_reply_is_never_flagged():
    """THE regression. A human answers, says a few words, then listens to
    a reply far longer than the 4s silence limit. Must never fire."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=5.5)

    # "Yep. Yes, sure." — brief, but unmistakably a person.
    d.on_remote_speech_start(at=6.0)
    d.on_remote_speech_end(at=7.2)

    # The agent then talks for twenty seconds straight.
    d.on_agent_speech_start(at=7.5)
    for t in (9.0, 12.0, 16.0, 20.0, 27.5):
        assert d.check(now=t) is None, f"fired at t={t} while the agent was speaking"
    d.on_agent_speech_end(at=27.5)

    # And the human takes a long beat before answering the question.
    for t in (30.0, 45.0, 120.0):
        assert d.check(now=t) is None, f"fired at t={t} after a human had spoken"


def test_the_latch_alone_survives_an_unreported_agent_turn():
    """Defence in depth: even if the agent-speaking signal never arrives
    (a missed event, a provider that doesn't report state), prior human
    speech alone must keep the detector silent forever."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    d.on_remote_speech_start(at=1.0)
    d.on_remote_speech_end(at=2.0)
    # No on_agent_speech_* calls at all, and a long quiet stretch.
    assert d.check(now=600.0) is None


def test_agent_speech_gating_alone_protects_a_human_who_has_not_spoken_yet():
    """The other half, isolated: nobody has spoken yet, but the agent is
    mid-greeting. Silence while we talk is not evidence of a recording."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    d.on_agent_speech_start(at=0.1)
    assert d.check(now=10.0) is None
    assert d.check(now=60.0) is None


def test_silence_is_still_caught_once_the_agent_stops_talking():
    """Gating must not blind the detector: a real voicemail box that sat
    quietly through our greeting still trips once we stop speaking."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    d.on_agent_speech_start(at=0.0)
    d.on_agent_speech_end(at=10.0)  # ten seconds of us talking
    # The window is the 4s AFTER we stop, not 4s from answer.
    assert d.check(now=13.9) is None
    assert d.check(now=14.1) == "voicemail_reached"


def test_a_stalled_llm_is_not_mistaken_for_agent_speech():
    """Only "speaking" freezes the clock. "thinking" is real dead air on
    the line, so it must still count toward the silence limit."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    # The entrypoint maps every non-"speaking" state to speech_end.
    d.on_agent_speech_end(at=0.0)
    assert d.check(now=4.1) == "voicemail_reached"


def test_an_ivr_monologue_is_still_caught_before_it_can_disarm():
    """The latch trips on speech END, and an IVR's unbroken greeting is
    flagged by the monologue limit before it ever pauses — so a recording
    cannot disarm the detector by talking."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    d.on_answer(at=0.0)
    d.on_remote_speech_start(at=0.2)
    assert d.check(now=6.5) == "ivr_reached"


def test_the_exact_failed_call_timeline_does_not_fire():
    """Replays f8150d92 to the millisecond, in the detector's own clock."""
    d = VoicemailDetector(silence_limit_s=4.0, monologue_limit_s=6.0)
    # Greeting spoken from ~9.3s to 14.887s, detector armed at greeting end.
    d.on_agent_speech_start(at=9.3)
    d.on_answer(at=14.887)
    d.on_agent_speech_end(at=14.887)
    # "Yep. Yes, sure." at 14.899s.
    d.on_remote_speech_start(at=14.899)
    d.on_remote_speech_end(at=15.9)
    # Agent's reply begins at 18.948s and runs long.
    d.on_agent_speech_start(at=18.948)
    # 18.948 + 4s is where the real call was killed.
    assert d.check(now=22.95) is None
    assert d.check(now=40.0) is None
