# Ported from truestar-voice (TrueStar) at 4c34cce: tests/test_call_metrics.py.
"""Tests for per-turn latency capture.

These exist because the numbers this module produces are the only real
measurement of what a caller hears — transcript timestamps cannot give it
(they are turn-start to turn-start, so they bundle dead air with the
agent's own speaking time). If the folding logic is wrong, the latency
budget silently goes back to being guesswork.
"""

from __future__ import annotations

from voice_caller.metrics import LatencyTracker


class _EOU:
    def __init__(self, eou: float, transcription: float | None = None):
        self.end_of_utterance_delay = eou
        if transcription is not None:
            self.transcription_delay = transcription


class _LLM:
    def __init__(self, ttft: float):
        self.ttft = ttft


class _TTS:
    def __init__(self, ttfb: float):
        self.ttfb = ttfb


def _complete_turn(tracker: LatencyTracker, eou=1.0, ttft=0.9, ttfb=0.2) -> None:
    tracker.on_metrics(_EOU(eou, transcription=0.1))
    tracker.on_metrics(_LLM(ttft))
    tracker.on_metrics(_TTS(ttfb))


def test_a_turn_is_recorded_only_once_all_three_legs_arrive():
    t = LatencyTracker(call_id="c1")
    t.on_metrics(_EOU(1.0))
    assert t.turns == []
    t.on_metrics(_LLM(0.9))
    assert t.turns == []
    t.on_metrics(_TTS(0.2))
    assert len(t.turns) == 1


def test_dead_air_is_the_sum_of_the_three_legs():
    t = LatencyTracker(call_id="c1")
    _complete_turn(t, eou=1.0, ttft=0.9, ttfb=0.2)
    # 1000 + 900 + 200; transcription_delay is reported but NOT added,
    # since it overlaps the endpointing wait rather than following it.
    assert t.turns[0].total_ms() == 2100.0


def test_metrics_are_converted_from_seconds_to_milliseconds():
    t = LatencyTracker(call_id="c1")
    _complete_turn(t, eou=1.5, ttft=0.75, ttfb=0.16)
    turn = t.turns[0]
    assert turn.eou_ms == 1500.0
    assert turn.llm_ttft_ms == 750.0
    assert turn.tts_ttfb_ms == 160.0


def test_consecutive_turns_do_not_bleed_into_each_other():
    t = LatencyTracker(call_id="c1")
    _complete_turn(t, eou=1.0, ttft=1.0, ttfb=0.2)
    _complete_turn(t, eou=2.0, ttft=2.0, ttfb=0.4)
    assert len(t.turns) == 2
    assert t.turns[0].eou_ms == 1000.0
    assert t.turns[1].eou_ms == 2000.0


def test_summary_uses_median_not_mean():
    """One content-filter retry adds ~8s; a mean would report a call that
    nobody experienced. Median keeps the figure representative."""
    t = LatencyTracker(call_id="c1")
    _complete_turn(t, eou=1.0, ttft=1.0, ttfb=0.2)
    _complete_turn(t, eou=1.0, ttft=1.0, ttfb=0.2)
    _complete_turn(t, eou=1.0, ttft=9.0, ttfb=0.2)  # the outlier
    assert t.summary()["llm_ttft_p50_ms"] == 1000


def test_summary_is_empty_before_any_complete_turn():
    t = LatencyTracker(call_id="c1")
    t.on_metrics(_EOU(1.0))
    assert t.summary() == {}


def test_a_malformed_metric_never_raises_into_a_live_call():
    class _Broken:
        @property
        def ttft(self):
            raise ValueError("boom")

    t = LatencyTracker(call_id="c1")
    t.on_metrics(_Broken())  # must not raise
    assert t.turns == []


def test_unknown_metric_types_are_ignored():
    class _Other:
        duration = 5.0

    t = LatencyTracker(call_id="c1")
    t.on_metrics(_Other())
    assert t.turns == []


def test_negative_timings_are_not_recorded():
    """LiveKit reports -1 for a leg that did not run (e.g. a cancelled
    preemptive generation). Treating that as a real 0ms would understate
    the budget."""
    t = LatencyTracker(call_id="c1")
    t.on_metrics(_EOU(1.0))
    t.on_metrics(_LLM(-1.0))
    t.on_metrics(_TTS(0.2))
    assert t.turns == []


def test_log_summary_without_turns_does_not_raise():
    LatencyTracker(call_id="c1").log_summary()
