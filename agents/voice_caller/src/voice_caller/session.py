"""One live phone conversation: join the call's LiveKit room and hold it with an AgentSession.

The lifecycle is ported from truestar-voice's ``campaign_agent.entrypoint`` (4c34cce):

    callee joins -> wait up to 5 s for their "Hello?" -> if silent, say "Hello?" and wait 2 s
    -> a short beat -> greeting -> conversation until end_call, the callee hangs up or says
    goodbye, the voicemail/IVR detector fires, or the call's time cap

Crewquarters changes:
- The broker dials and hands over a token for this room only (there is no LiveKit worker job).
- Speech-to-text, the LLM, and speech go through the broker's OpenAI-compatible facade.
- The end-of-turn model runs in process (``turn_detection``).
- The greeting says the caller is automated.
- No delivery markup reaches the local voice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import statistics
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from livekit.agents import Agent, ModelSettings, TurnHandlingOptions, function_tool
from livekit.agents import RunContext as ToolContext
from livekit.agents.llm import ChatContext, ChatMessage

from caller_agent.rows import ContactRow
from crewquarters import RunContext
from crewquarters.errors import PlatformError
from crewquarters.voice import VoiceCall
from voice_caller.config import VoiceCallerConfig
from voice_caller.metrics import LatencyTracker
from voice_caller.prompts import (
    DNC_SIGN_OFF,
    HELLO_PROBE,
    SIGN_OFFS,
    build_greeting,
    build_system_prompt,
)
from voice_caller.safety import sanitize_user_text
from voice_caller.texture import TextureState, humanize
from voice_caller.transcript import Transcript
from voice_caller.turn_detection import load_turn_detector
from voice_caller.voicemail import VoicemailDetector

log = logging.getLogger(__name__)

# Timings from the reference agent (measured on real calls; see its comments).
GREETING_PICKUP_WAIT_S = 5.0
HELLO_PROBE_WAIT_S = 2.0
GREETING_LATE_HELLO_EXTRA_S = 2.0
GREETING_BEAT_S = 0.4
HANGUP_GRACE_S = 1.0
VOICEMAIL_SILENCE_LIMIT_S = 4.0
VOICEMAIL_MONOLOGUE_LIMIT_S = 6.0
ANSWER_POLL_S = 0.5
OUT_OF_TIME = "I'm afraid I'm out of time. Thanks so much for talking with me. Goodbye."

# The callee ending the call in so many words (ported). "Don't call" is also a do-not-call request.
_END_RE = re.compile(
    r"(?:"
    r"^\s*(?:okay\s+|ok\s+|alright\s+|well\s+)?(?:bye|goodbye|cheers)\b[.!]?\s*$|"
    r"\b(?:bye|goodbye)\s*[.!]?\s*$|"
    r"\bnot\s+interested\b|"
    r"\b(?:take|remove)\s+me\s+off\b|"
    r"\bdo\s*n[o']?t\s+call\s+(?:me\s+)?(?:again|back)\b|"
    r"\bstop\s+calling\b|"
    r"\bhang\s+up\b"
    r")",
    re.IGNORECASE,
)
_DNC_RE = re.compile(r"\b(?:do\s*n[o']?t\s+call|stop\s+calling|(?:take|remove)\s+me\s+off)\b", re.I)


@dataclass
class CallReport:
    """What happened on one call, for the outcome and the result row."""

    call_id: str
    answered: bool
    ended_by: str
    final_state: str
    transcript: Transcript = field(default_factory=Transcript)
    duration_seconds: int | None = None
    error_code: str | None = None
    latency_p50_ms: float | None = None


class CallRunner(Protocol):
    async def prepare(self, ctx: RunContext[Any], config: VoiceCallerConfig) -> None: ...

    async def run_call(
        self,
        ctx: RunContext[Any],
        call: VoiceCall,
        contact: ContactRow,
        config: VoiceCallerConfig,
    ) -> CallReport: ...


async def plain_speech(text: AsyncIterable[str]) -> AsyncIterator[str]:
    """Drop anything in [brackets] or <tags> from a text stream, even across chunk boundaries.

    The local voice reads markup aloud; the prompt forbids it, and this makes sure of it."""
    closing: str | None = None
    async for chunk in text:
        kept = []
        for char in chunk:
            if closing is not None:
                if char == closing:
                    closing = None
                continue
            if char in "[<":
                closing = "]" if char == "[" else ">"
                continue
            kept.append(char)
        if kept:
            yield "".join(kept)


class VoiceCallAgent(Agent):
    """The conversation: the call's system prompt, pre-LLM sanitizing, and an end_call tool."""

    def __init__(self, instructions: str, hang_up: Callable[[], Awaitable[None]]) -> None:
        super().__init__(instructions=instructions)
        self._hang_up = hang_up
        self.raw_user_text: dict[str, str] = {}  # item id -> unmasked text, for the transcript
        self._texture = TextureState()

    def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings) -> Any:
        speakable = humanize(plain_speech(text), self._texture, sounds=False)
        return Agent.default.tts_node(self, speakable, model_settings)

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        text = new_message.text_content or ""
        cleaned, changed = sanitize_user_text(text)
        if changed:
            self.raw_user_text[new_message.id] = text
            new_message.content = [cleaned]

    @function_tool()
    async def end_call(self, context: ToolContext[Any]) -> None:
        """Hang up the phone call. Call this in the SAME turn as your goodbye whenever the person
        says they are done, busy, not interested, or asks you to stop or not to call again, and
        once your questions are covered and you have thanked them."""
        await context.wait_for_playout()
        await self._hang_up()


def _text_of(item: Any) -> str:
    content = getattr(item, "content", None)
    if isinstance(content, list):
        parts = [p if isinstance(p, str) else str(getattr(p, "text", "") or "") for p in content]
        return " ".join(p for p in parts if p).strip()
    return str(content or "").strip()


class LiveKitCallRunner:
    """Holds each approved call in its LiveKit room with the local models."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()  # noqa: S311 - varies greetings, not a security use
        self._vad: Any = None
        self._turn_detector: Any = None
        self._profiles: tuple[str, str, str] = ("", "", "")

    async def prepare(self, ctx: RunContext[Any], config: VoiceCallerConfig) -> None:
        from livekit.plugins import silero

        self._profiles = (
            ctx.llm.resolve_profile(config.stt_profile),
            ctx.llm.resolve_profile(config.model_profile),
            ctx.llm.resolve_profile(config.tts_profile),
        )
        self._vad = await asyncio.to_thread(silero.VAD.load)
        self._turn_detector = await load_turn_detector()
        if self._turn_detector is None:
            await ctx.events.log(
                "warning", "End-of-turn model unavailable; using pause-based endpointing"
            )

    def _models(self, ctx: RunContext[Any], config: VoiceCallerConfig) -> tuple[Any, Any, Any]:
        from livekit.plugins import openai

        endpoint = ctx.model_endpoint()
        stt_profile, llm_profile, tts_profile = self._profiles
        stt = openai.STT(
            model=stt_profile,
            language="en",
            use_realtime=False,
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
        )
        llm = openai.LLM(
            model=llm_profile,
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
            temperature=0.6,
            max_completion_tokens=95,  # the reference's measured cap: short, complete turns
        )
        tts = openai.TTS(
            model=tts_profile,
            voice=config.voice,
            response_format="pcm",
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
        )
        return stt, llm, tts

    async def run_call(
        self,
        ctx: RunContext[Any],
        call: VoiceCall,
        contact: ContactRow,
        config: VoiceCallerConfig,
    ) -> CallReport:
        from livekit import rtc

        if call.room is None or call.room.url.startswith("offline://"):
            await ctx.voice.hangup(call.id)
            await ctx.events.log(
                "error",
                "No media server: start a local LiveKit server (make voice-up) to hold calls",
                call_id=call.id,
            )
            return CallReport(call.id, False, "error", "failed", error_code="NO_MEDIA_SERVER")
        room = rtc.Room()
        try:
            await room.connect(call.room.url, call.room.token)
        except Exception as exc:
            log.warning("call %s: could not join the room: %s", call.id, exc)
            await ctx.voice.hangup(call.id)
            return CallReport(call.id, False, "error", "failed", error_code="MEDIA_CONNECT_FAILED")
        try:
            state = await self._wait_for_answer(ctx, call, room, config)
            if state != "answered":
                return CallReport(call.id, False, state, state)
            return await self._converse(ctx, call, contact, config, room)
        finally:
            with contextlib.suppress(Exception):
                await room.disconnect()

    async def _wait_for_answer(
        self, ctx: RunContext[Any], call: VoiceCall, room: Any, config: VoiceCallerConfig
    ) -> str:
        """``answered`` once the callee is in the room, or the broker's final state."""
        callee = call.callee_identity or "callee"
        deadline = time.monotonic() + config.ring_timeout_seconds + 10
        while time.monotonic() < deadline:
            if any(p.identity == callee for p in room.remote_participants.values()):
                return "answered"
            current = await ctx.voice.get(call.id)
            if current.ended:
                return current.state
            await asyncio.sleep(ANSWER_POLL_S)
        ended = await ctx.voice.hangup(call.id)
        return "no-answer" if ended.state in {"canceled", "no-answer"} else ended.state

    async def _converse(
        self,
        ctx: RunContext[Any],
        call: VoiceCall,
        contact: ContactRow,
        config: VoiceCallerConfig,
        room: Any,
    ) -> CallReport:
        from livekit.agents import AgentSession, room_io

        loop = asyncio.get_running_loop()
        done: asyncio.Future[str] = loop.create_future()
        tasks: set[asyncio.Task[Any]] = set()
        transcript = Transcript()
        latency = LatencyTracker(call_id=call.id)
        detector = VoicemailDetector(
            silence_limit_s=VOICEMAIL_SILENCE_LIMIT_S,
            monologue_limit_s=VOICEMAIL_MONOLOGUE_LIMIT_S,
        )
        started = time.monotonic()
        errors: list[str] = []

        def elapsed() -> float:
            return time.monotonic() - started

        def finish(reason: str) -> None:
            if not done.done():
                done.set_result(reason)

        def spawn(coro: Any) -> None:
            task = loop.create_task(coro)
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        async def hang_up_from_tool() -> None:
            await asyncio.sleep(HANGUP_GRACE_S)
            finish("agent_ended")

        agent = VoiceCallAgent(build_system_prompt(config, contact.name), hang_up_from_tool)
        stt, llm, tts = self._models(ctx, config)
        turn_handling: TurnHandlingOptions = {
            "endpointing": {"min_delay": 0.3, "max_delay": 1.2},
            "preemptive_generation": {"enabled": True, "preemptive_tts": True},
        }
        if self._turn_detector is not None:
            turn_handling["turn_detection"] = self._turn_detector
        session: AgentSession[Any] = AgentSession(
            stt=stt, llm=llm, tts=tts, vad=self._vad, turn_handling=turn_handling
        )
        heard_them = asyncio.Event()
        they_spoke = {"flag": False}
        wrapping = {"flag": False}

        async def wrap_up(line: str, reason: str) -> None:
            """The callee ended it in so many words: one short closer, a beat, hang up."""
            if wrapping["flag"] or done.done():
                return
            wrapping["flag"] = True
            with contextlib.suppress(Exception):
                await session.interrupt(force=True)
            with contextlib.suppress(Exception):
                await session.say(line, allow_interruptions=False)
            await asyncio.sleep(HANGUP_GRACE_S)
            finish(reason)

        @session.on("metrics_collected")
        def _on_metrics(event: Any) -> None:
            latency.on_metrics(getattr(event, "metrics", event))

        @session.on("user_state_changed")
        def _on_user_state(event: Any) -> None:
            state = getattr(event, "new_state", None)
            if state == "speaking":
                they_spoke["flag"] = True
                detector.on_remote_speech_start(at=elapsed())
            elif state in ("listening", "away"):
                detector.on_remote_speech_end(at=elapsed())
                if they_spoke["flag"]:
                    heard_them.set()

        @session.on("agent_state_changed")
        def _on_agent_state(event: Any) -> None:
            if getattr(event, "new_state", None) == "speaking":
                detector.on_agent_speech_start(at=elapsed())
            else:
                detector.on_agent_speech_end(at=elapsed())

        @session.on("conversation_item_added")
        def _on_item(event: Any) -> None:
            item = getattr(event, "item", None)
            role = getattr(item, "role", None)
            text = _text_of(item)
            if not text:
                return
            if role == "user":
                raw = agent.raw_user_text.pop(getattr(item, "id", ""), text)
                transcript.add("callee", raw)
                if _END_RE.search(raw):
                    closer = DNC_SIGN_OFF if _DNC_RE.search(raw) else self._rng.choice(SIGN_OFFS)
                    spawn(wrap_up(closer, "callee_ended"))
            elif role == "assistant":
                transcript.add("agent", text)

        @session.on("error")
        def _on_error(event: Any) -> None:
            error = getattr(event, "error", event)
            errors.append(type(error).__name__)
            log.warning("call %s session error: %s", call.id, str(error)[:300])
            if getattr(error, "recoverable", True) is False:
                finish("error")

        @session.on("close")
        def _on_close(event: Any) -> None:
            finish("error" if getattr(event, "error", None) else "session_closed")

        def _on_left(participant: Any) -> None:
            if participant.identity == (call.callee_identity or "callee"):
                finish("callee_hung_up")

        room.on("participant_disconnected", _on_left)
        room.on("disconnected", lambda *_: finish("room_closed"))

        options = room_io.RoomOptions(
            participant_identity=call.callee_identity or "callee",
            close_on_disconnect=False,
            delete_room_on_close=False,
        )
        await session.start(agent, room=room, room_options=options)

        async def their_turn(within: float) -> bool:
            try:
                await asyncio.wait_for(heard_them.wait(), timeout=within)
                return True
            except TimeoutError:
                if not they_spoke["flag"]:
                    return False
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(heard_them.wait(), timeout=GREETING_LATE_HELLO_EXTRA_S)
                return True

        reason = "error"
        try:
            if not await their_turn(GREETING_PICKUP_WAIT_S):
                they_spoke["flag"] = False
                heard_them.clear()
                with contextlib.suppress(Exception):
                    await session.say(HELLO_PROBE, allow_interruptions=True)
                await their_turn(HELLO_PROBE_WAIT_S)
            await asyncio.sleep(GREETING_BEAT_S)
            if not done.done():
                await session.say(build_greeting(config, contact.name, self._rng))
                detector.on_answer(at=elapsed())
                spawn(self._watch_for_machine(detector, elapsed, done, finish))
            try:
                reason = await asyncio.wait_for(asyncio.shield(done), config.max_call_seconds)
            except TimeoutError:
                await wrap_up(OUT_OF_TIME, "time_limit")
                reason = await done
        except Exception as exc:
            log.exception("call %s failed: %s", call.id, exc)
            errors.append(type(exc).__name__)
            finish("error")
            reason = await done
        finally:
            for task in list(tasks):
                task.cancel()
            with contextlib.suppress(Exception):
                await session.aclose()
        final = await self._hang_up(ctx, call)
        totals = [turn.total_ms() for turn in latency.turns]
        return CallReport(
            call_id=call.id,
            answered=True,
            ended_by=reason,
            final_state=final.state if final else "completed",
            transcript=transcript,
            duration_seconds=final.duration_seconds if final else None,
            error_code="MODEL_ERROR" if reason == "error" else None,
            latency_p50_ms=statistics.median(totals) if totals else None,
        )

    @staticmethod
    async def _watch_for_machine(
        detector: VoicemailDetector,
        elapsed: Callable[[], float],
        done: asyncio.Future[str],
        finish: Callable[[str], None],
    ) -> None:
        """Poll the (pure) voicemail/IVR detector against real time; hang up on a machine."""
        while not done.done():
            await asyncio.sleep(0.25)
            reason = detector.check(now=elapsed())
            if reason is not None:
                finish(reason)
                return

    @staticmethod
    async def _hang_up(ctx: RunContext[Any], call: VoiceCall) -> VoiceCall | None:
        try:
            return await ctx.voice.hangup(call.id)
        except PlatformError as exc:
            log.warning("call %s: hangup failed: %s", call.id, exc)
            return None
