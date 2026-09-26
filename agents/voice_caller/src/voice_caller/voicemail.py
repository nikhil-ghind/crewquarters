"""Voicemail / IVR detection for outbound campaign calls.

Retell gave us this for free; LiveKit does not. It matters here because most
reachable numbers on a sourced brief are shared corporate work lines that
answer with an IVR tree, and an agent that interviews a recorded greeting
for ten minutes bills real money for nothing.

Heuristic:
  - No human speech within `silence_limit_s` of answer -> "voicemail_reached"
  - Far end speaks continuously for more than `monologue_limit_s` with no
    pause -> "ivr_reached"

Two properties keep the heuristic from firing on live humans (call
f8150d92, where it hung up on a consenting expert mid-sentence):

  - The silence clock does not run while the AGENT is speaking. Silence
    while we talk is the other party listening, which is the correct
    behaviour, not evidence of a recording. The caller feeds this in via
    on_agent_speech_start/end (wired to LiveKit's agent_state_changed).
  - Once the far end has genuinely spoken, the detector DISARMS
    permanently: check() returns None forever. Voicemail and IVR
    detection is only meaningful in the opening seconds, before anyone
    has said anything real. A human who says "Yep, sure" and then listens
    to a long reply must never be reclassified as a recording.

VoicemailDetector is a PURE class: it holds no wall-clock state of its own
and takes every timestamp as an explicit `at=`/`now=` float from the caller.
That's what makes it unit-testable without real time passing — the caller
(campaign_agent.py) is responsible for feeding it real elapsed seconds from
a monotonic clock tied to actual session events.
"""
# Ported from truestar-voice (TrueStar) at 4c34cce: src/voicemail.py.
# Changes for Crewquarters are marked "Crewquarters:".

from __future__ import annotations

import re


class VoicemailDetector:
    def __init__(self, silence_limit_s: float = 4.0, monologue_limit_s: float = 6.0) -> None:
        self._silence_limit_s = silence_limit_s
        self._monologue_limit_s = monologue_limit_s

        self._answered_at: float | None = None
        # Set while the far end is mid-utterance (start seen, no end yet).
        self._speech_started_at: float | None = None
        # True once the far end has produced any speech at all. Once
        # speech has happened, the "silence after answer" check can never
        # fire again for this call, even if a later pause pushes `now`
        # past the original silence window.
        self._has_spoken: bool = False
        # Permanent disarm. Set the moment the far end finishes a genuine
        # utterance: from then on this is a conversation with a person,
        # and no later silence or monologue can mean voicemail/IVR.
        self._disarmed: bool = False
        # Set while WE are talking. Silence during our own speech is the
        # other party listening politely, so it must not accumulate.
        self._agent_speaking_since: float | None = None
        # Total seconds the agent has spent speaking since on_answer, used
        # to shift the silence deadline forward by exactly that much.
        self._agent_speech_total: float = 0.0

    def on_answer(self, at: float) -> None:
        self._answered_at = at
        self._speech_started_at = None
        self._has_spoken = False
        self._disarmed = False
        self._agent_speaking_since = None
        self._agent_speech_total = 0.0

    def on_agent_speech_start(self, at: float) -> None:
        """The agent began speaking — freeze the silence clock."""
        if self._agent_speaking_since is None:
            self._agent_speaking_since = at

    def on_agent_speech_end(self, at: float) -> None:
        """The agent stopped speaking — bank that span and resume."""
        if self._agent_speaking_since is not None:
            self._agent_speech_total += max(0.0, at - self._agent_speaking_since)
            self._agent_speaking_since = None

    def on_remote_speech_start(self, at: float) -> None:
        self._speech_started_at = at
        self._has_spoken = True

    def on_remote_speech_end(self, at: float) -> None:
        # A pause after real speech is proof of a human turn-taking
        # rhythm. Clear the in-progress monologue window so it can't fire
        # retroactively, and disarm for good.
        self._speech_started_at = None
        if self._has_spoken:
            self._disarmed = True

    def check(self, now: float) -> str | None:
        if self._answered_at is None:
            return None

        # A human has already spoken and yielded the floor. Nothing that
        # happens later can make this a voicemail box or an IVR tree.
        if self._disarmed:
            return None

        # While the agent is mid-utterance, neither timer is meaningful:
        # the far end is listening to us, not failing to speak.
        if self._agent_speaking_since is not None:
            return None

        # Unbroken monologue: speech started and hasn't paused yet.
        if self._speech_started_at is not None:
            if (now - self._speech_started_at) > self._monologue_limit_s:
                return "ivr_reached"
            return None

        # No speech at all yet, and we've been sitting on an answered
        # line past the silence window — nobody picked up, it's a
        # recording that hasn't started, or dead air.
        # The deadline slides forward by however long we have been
        # talking, so only silence that was genuinely the far end's to
        # fill counts against them.
        deadline = self._answered_at + self._agent_speech_total + self._silence_limit_s
        if not self._has_spoken and now > deadline:
            return "voicemail_reached"

        return None


# Crewquarters: a transcript signal, independent of the audio timing above. The audio heuristic
# disarms at the far end's first pause, and a spoken voicemail greeting has pauses, so a greeting
# that talks for twenty seconds could otherwise be treated as a person.
_VOICEMAIL_PHRASES = re.compile(
    r"\b(?:"
    r"leave (?:a|your) (?:message|name)|after the (?:tone|beep)|"
    r"(?:you(?:'ve| have)|you) reached (?:the )?(?:voicemail|voice mail|mailbox)|"
    r"(?:is|are) not available|"
    r"(?:can't|cannot|can not) (?:take|come to) (?:your|the) (?:call|phone)|"
    r"voice messaging system|record your message|mailbox is full"
    r")\b",
    re.IGNORECASE,
)


def looks_like_voicemail(text: str) -> bool:
    """True when an opening utterance reads like a voicemail or answering-service greeting."""
    return bool(text) and bool(_VOICEMAIL_PHRASES.search(text))
