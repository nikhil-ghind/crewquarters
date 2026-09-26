"""Per-component latency capture for a campaign call.

Why this exists: the turn gaps visible in `campaign_call_turns` are
turn-START to turn-START, so they bundle the silence the caller hears
together with however long the agent then spends speaking. A 140-char
reply takes ~9 s to say at 8 kHz, which is why measured "gaps" of 9-11 s
looked like latency but were mostly speech. Reading real latency off the
transcript is therefore impossible; it has to come from the pipeline.

LiveKit already computes exactly the breakdown we want and emits it on
`metrics_collected`, but nothing subscribed to it, so every figure in the
latency budget was a bench estimate rather than a measurement from a real
call. `LatencyTracker` subscribes once and folds the four metric types
into one per-turn record:

    end_of_utterance_delay  -> STT endpointing (the wait after speech ends)
    transcription_delay     -> STT transcription tail
    LLMMetrics.ttft         -> time to first token
    TTSMetrics.ttfb         -> time to first audio byte

The sum of those three is the dead air the caller actually experiences.

Emission is a single INFO log line per turn so it survives the container's
default LOG_LEVEL=info — the framework's own preemptive-generation
telemetry is logger.debug and was invisible in production for that exact
reason.
"""
# Ported from truestar-voice (TrueStar) at 4c34cce: src/call_metrics.py.
# Changes for Crewquarters are marked "Crewquarters:".

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class _Turn:
    """One assistant turn's component timings, in milliseconds."""

    eou_ms: float | None = None
    transcription_ms: float | None = None
    llm_ttft_ms: float | None = None
    tts_ttfb_ms: float | None = None

    def is_complete(self) -> bool:
        """True once the three legs that make up dead air have landed.

        `transcription_ms` is deliberately not required: it arrives with
        the EOU metric when the STT reports it, but a plugin that does
        not populate it must not stall the record forever.
        """
        return (
            self.eou_ms is not None
            and self.llm_ttft_ms is not None
            and self.tts_ttfb_ms is not None
        )

    def total_ms(self) -> float:
        return (self.eou_ms or 0.0) + (self.llm_ttft_ms or 0.0) + (self.tts_ttfb_ms or 0.0)


@dataclass
class LatencyTracker:
    """Folds LiveKit metric events into per-turn latency records.

    One instance per call. `call_id` is carried so a log line can be tied
    back to its call (Crewquarters: the broker's voice call id).
    """

    call_id: str
    turns: list[_Turn] = field(default_factory=list)
    _current: _Turn | None = None

    def _turn(self) -> _Turn:
        if self._current is None:
            self._current = _Turn()
        return self._current

    def _flush_if_ready(self) -> None:
        turn = self._current
        if turn is None or not turn.is_complete():
            return
        self.turns.append(turn)
        self._current = None
        log.info(
            "call latency turn=%d eou=%.0fms transcription=%s llm_ttft=%.0fms "
            "tts_ttfb=%.0fms dead_air=%.0fms call=%s",
            len(self.turns),
            turn.eou_ms or 0.0,
            f"{turn.transcription_ms:.0f}ms" if turn.transcription_ms is not None else "n/a",
            turn.llm_ttft_ms or 0.0,
            turn.tts_ttfb_ms or 0.0,
            turn.total_ms(),
            self.call_id,
        )

    def on_metrics(self, metrics: object) -> None:
        """Handle one `metrics_collected` payload.

        Dispatches on attribute presence rather than isinstance so a
        metrics class moving between livekit-agents versions degrades to
        a missing field instead of an exception on a live call.
        """
        try:
            # EOU: the endpointing wait, plus the STT's own transcription tail.
            eou = getattr(metrics, "end_of_utterance_delay", None)
            if eou is not None:
                self._turn().eou_ms = float(eou) * 1000.0
                transcription = getattr(metrics, "transcription_delay", None)
                if transcription is not None:
                    self._turn().transcription_ms = float(transcription) * 1000.0

            # LLM: time to first token.
            ttft = getattr(metrics, "ttft", None)
            if ttft is not None and float(ttft) >= 0:
                self._turn().llm_ttft_ms = float(ttft) * 1000.0

            # TTS: time to first audio byte.
            ttfb = getattr(metrics, "ttfb", None)
            if ttfb is not None and float(ttfb) >= 0:
                self._turn().tts_ttfb_ms = float(ttfb) * 1000.0

            self._flush_if_ready()
        except Exception as exc:  # pragma: no cover - defensive
            # Never let telemetry break a call in progress.
            log.debug("latency metric ignored (non-fatal): %s", exc)

    def summary(self) -> dict[str, float | int]:
        """Median per-component timings across the call.

        Median rather than mean because one content-filter retry adds
        ~8 s and would drag an average far from what the caller typically
        heard.
        """
        if not self.turns:
            return {}

        def _median(values: list[float]) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            mid = len(ordered) // 2
            if len(ordered) % 2:
                return ordered[mid]
            return (ordered[mid - 1] + ordered[mid]) / 2.0

        return {
            "turns": len(self.turns),
            "eou_p50_ms": round(_median([t.eou_ms or 0.0 for t in self.turns])),
            "llm_ttft_p50_ms": round(_median([t.llm_ttft_ms or 0.0 for t in self.turns])),
            "tts_ttfb_p50_ms": round(_median([t.tts_ttfb_ms or 0.0 for t in self.turns])),
            "dead_air_p50_ms": round(_median([t.total_ms() for t in self.turns])),
        }

    def log_summary(self) -> None:
        summary = self.summary()
        if not summary:
            log.info("call latency summary: no complete turns call=%s", self.call_id)
            return
        log.info(
            "call latency summary turns=%d eou_p50=%dms llm_ttft_p50=%dms "
            "tts_ttfb_p50=%dms dead_air_p50=%dms call=%s",
            summary["turns"],
            summary["eou_p50_ms"],
            summary["llm_ttft_p50_ms"],
            summary["tts_ttfb_p50_ms"],
            summary["dead_air_p50_ms"],
            self.call_id,
        )
