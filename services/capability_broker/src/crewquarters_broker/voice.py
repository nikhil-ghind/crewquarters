"""Realtime phone conversations: Twilio Media Streams <-> local speech-to-text, a local
chat model, and streaming text-to-speech.

The owner starts a call to an allowed number from the UI (control API -> broker). Twilio
first speaks the disclosure itself, then connects the call's audio to
``wss://<callback origin>/api/v1/callbacks/twilio/media/{id}``. For each caller turn:

1. an energy-based voice activity detector segments the caller's speech (20 ms frames);
2. the utterance goes to the speech-to-text model (``CQ_VOICE_ASR_MODEL``);
3. the local chat model (``CQ_VOICE_LLM_PROFILE``) streams a short reply;
4. its words stream into the text-to-speech model (``CQ_VOICE_TTS_MODEL``) as they arrive,
   and the audio goes back to Twilio as 8 kHz mu-law while it is generated.

If the caller talks over the assistant, Twilio's buffered audio is cleared and the reply is
cancelled (barge-in). All model calls go through the model gateway with the voice
credential and hold one lease per model for the call (``voice:<id>``), released at the end.

Security and privacy:

* The stream URL carries no secret; each call has an unguessable one-time token that
  Twilio returns in the stream's ``start`` message (a TwiML ``<Parameter>``), and live
  calls must also present a valid ``X-Twilio-Signature`` on the WebSocket handshake.
* Destinations are limited to ``CQ_TWILIO_ALLOWED_NUMBERS``; one call runs at a time.
* Audio is never stored. The transcript is kept in memory for the owner's live view and
  is dropped when the session expires; the audit log records only the masked number,
  duration, and turn count.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import httpx
import numpy as np
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.twilio import API_URL, TelephonyService, signature
from crewquarters_broker.voice_audio import (
    FRAME_MS,
    FRAME_SAMPLES,
    TELEPHONE_RATE,
    Downsampler,
    VoiceActivityDetector,
    rms,
    ulaw_decode,
    ulaw_encode,
    wav_bytes,
)
from crewquarters_shared import audit
from crewquarters_shared.errors import PlatformError, conflict, invalid, not_found
from crewquarters_shared.redaction import mask_phone
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger("crewquarters.broker.voice")

MEDIA_PATH = "/api/v1/callbacks/twilio/media"
STATUS_PATH = "/api/v1/callbacks/twilio/voice-status"
DISCLOSURE = (
    "Hello. This is an automated call from Crewquarters. You are speaking with an AI "
    "assistant, and the conversation is transcribed for its owner."
)
GREETING = "Hi! How can I help you today?"
TIMEOUT_GOODBYE = "We have reached the time limit for this call. Thank you, goodbye."
SYSTEM_PROMPT = (
    "You are a friendly voice assistant speaking with someone on a phone call. Reply in one "
    "or two short, natural spoken sentences. Never use lists, markdown, emoji, or symbols "
    "that cannot be spoken. If you did not understand, ask the caller to repeat. Speak "
    "English."
)
MAX_INSTRUCTIONS_CHARS = 1000
MAX_HISTORY_TURNS = 12
SESSION_KEEP_SECONDS = 3600
TERMINAL_STATES = frozenset({"ended", "failed"})
TWILIO_TERMINAL = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})
_UNSPEAKABLE = re.compile(r"[*_#`~>|\[\]{}<]")


@dataclass
class Turn:
    role: str  # caller | assistant
    text: str
    at: datetime
    interrupted: bool = False
    timings: dict[str, int] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "at": self.at.isoformat(),
            "interrupted": self.interrupted,
            "timings": self.timings,
        }


@dataclass
class VoiceSession:
    id: str
    token: str
    user_id: uuid.UUID
    to_masked: str
    voice: str
    instructions: str
    simulated: bool
    state: str = "created"  # created | dialing | ringing | connected | ended | failed
    twilio_sid: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None
    error: str | None = None
    turns: list[Turn] = field(default_factory=list)
    hangup: asyncio.Event = field(default_factory=asyncio.Event)
    stream_used: bool = False

    def view(self) -> dict[str, Any]:
        duration = None
        if self.connected_at is not None:
            duration = int(((self.ended_at or utcnow()) - self.connected_at).total_seconds())
        return {
            "id": self.id,
            "state": self.state,
            "to": self.to_masked,
            "voice": self.voice,
            "simulated": self.simulated,
            "createdAt": self.created_at.isoformat(),
            "connectedAt": self.connected_at.isoformat() if self.connected_at else None,
            "endedAt": self.ended_at.isoformat() if self.ended_at else None,
            "durationSeconds": duration,
            "endReason": self.end_reason,
            "error": self.error,
            "turns": [t.view() for t in self.turns],
        }


def stream_twiml(stream_url: str, token: str, session_id: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f"<Say>{escape(DISCLOSURE)}</Say>"
        f"<Connect><Stream url={quoteattr(stream_url)}>"
        f'<Parameter name="token" value={quoteattr(token)}/>'
        f'<Parameter name="session" value={quoteattr(session_id)}/>'
        "</Stream></Connect><Hangup/></Response>"
    )


def split_words(buffer: str) -> tuple[list[str], str]:
    """Complete words of a streamed text, and the unfinished tail."""
    cut = max(buffer.rfind(" "), buffer.rfind("\n"))
    if cut < 0:
        return [], buffer
    return buffer[:cut].split(), buffer[cut + 1 :]


def speakable(text: str) -> str:
    return " ".join(_UNSPEAKABLE.sub(" ", text).split())


class VoiceService:
    def __init__(
        self,
        settings: BrokerSettings,
        telephony: TelephonyService,
        http: httpx.AsyncClient,
        sessions: async_sessionmaker[AsyncSession],
        gateway_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.telephony = telephony
        self.http = http  # Twilio REST
        self.db = sessions
        token = settings.internal_service_token.get_secret_value()
        self.gateway_headers = {
            "Authorization": f"Bearer {token}",
            "X-Voice-Client-Token": settings.voice_client_token.get_secret_value(),
        }
        base = settings.model_gateway_url.rstrip("/")
        self.gateway = httpx.AsyncClient(
            base_url=f"{base}/internal/v1",
            headers=self.gateway_headers,
            timeout=httpx.Timeout(settings.voice_gateway_timeout_seconds, connect=5.0),
            transport=gateway_transport,
        )
        self.gateway_ws = base.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        self.calls: dict[str, VoiceSession] = {}

    async def close(self) -> None:
        for session in self.calls.values():
            session.hangup.set()
        await self.gateway.aclose()

    # --- Owner actions (control API -> /internal/v1/voice) ------------------------------

    async def create(
        self,
        user_id: uuid.UUID,
        to: str,
        voice: str,
        instructions: str,
        simulate: bool = False,
    ) -> dict[str, Any]:
        """Start a call (or, with ``simulate``, a session a test client connects to as if it
        were Twilio). One call at a time: the text-to-speech model serves one stream."""
        self._expire()
        if len(instructions) > MAX_INSTRUCTIONS_CHARS:
            raise invalid("INVALID_REQUEST", "Instructions are limited to 1000 characters.")
        if any(s.state not in TERMINAL_STATES for s in self.calls.values()):
            raise conflict("VOICE_CALL_ACTIVE", "A voice call is already in progress.")
        creds: dict[str, str] = {}
        base = self.settings.twilio_base_url()
        if not simulate:
            # Everything that can refuse the call is checked before the session exists.
            self.telephony._check_destination(to)
            creds = await self.telephony._credentials()
            if not base.startswith("https://"):
                raise PlatformError(
                    "NEEDS_CONFIGURATION",
                    "Realtime calls need a public https callback address "
                    "(CQ_TWILIO_CALLBACK_BASE_URL and the callback tunnel).",
                    409,
                )
        session = VoiceSession(
            id=uuid.uuid4().hex,
            token=secrets.token_urlsafe(32),
            user_id=user_id,
            to_masked=mask_phone(to) if to else "simulated",
            voice=voice,
            instructions=instructions.strip(),
            simulated=simulate,
        )
        self.calls[session.id] = session
        if simulate:
            session.state = "dialing"
            await self._audit(session, "voice_call.simulated", "success")
            view = session.view()
            view["streamPath"] = f"{MEDIA_PATH}/{session.id}"
            view["token"] = session.token
            return view
        stream_url = "wss://" + base.removeprefix("https://") + f"{MEDIA_PATH}/{session.id}"
        session.state = "dialing"
        try:
            resp = await self.http.post(
                f"{API_URL}/Accounts/{creds['accountSid']}/Calls.json",
                data={
                    "To": to,
                    "From": creds["fromNumber"],
                    "Twiml": stream_twiml(stream_url, session.token, session.id),
                    "Timeout": "30",
                    "TimeLimit": str(self.settings.voice_max_call_seconds + 60),
                    "StatusCallback": f"{base}{STATUS_PATH}/{session.id}",
                    "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                },
                auth=(creds["accountSid"], creds["authToken"]),
            )
        except httpx.HTTPError:
            self._end(session, "failed", "twilio_unreachable")
            raise PlatformError("PROVIDER_UNAVAILABLE", "Twilio is unreachable.", 503) from None
        if resp.status_code >= 300:
            self._end(session, "failed", "twilio_rejected")
            session.error = f"Twilio did not accept the call ({resp.status_code})."
            await self._audit(session, "voice_call.started", "failure")
            raise PlatformError(
                "PROVIDER_ERROR",
                "Twilio did not accept the call.",
                502,
                {"providerStatus": resp.status_code},
            )
        session.twilio_sid = str(resp.json().get("sid") or "") or None
        await self._audit(session, "voice_call.started", "success")
        return session.view()

    def get(self, session_id: str) -> dict[str, Any]:
        return self._session(session_id).view()

    async def end(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        session.hangup.set()
        if session.twilio_sid and session.state not in TERMINAL_STATES:
            with contextlib.suppress(httpx.HTTPError, PlatformError):
                creds = await self.telephony._credentials()
                await self.http.post(
                    f"{API_URL}/Accounts/{creds['accountSid']}/Calls/{session.twilio_sid}.json",
                    data={"Status": "completed"},
                    auth=(creds["accountSid"], creds["authToken"]),
                )
        if session.state not in ("connected", *TERMINAL_STATES):
            self._end(session, "ended", "hung_up_by_owner")
        return session.view()

    # --- Twilio callbacks -----------------------------------------------------------------

    async def status_callback(self, session_id: str, params: Mapping[str, str]) -> None:
        session = self.calls.get(session_id)
        if session is None or session.state in TERMINAL_STATES:
            return
        status = params.get("CallStatus", "")
        if status == "ringing" and session.state == "dialing":
            session.state = "ringing"
        elif status in TWILIO_TERMINAL and session.state != "connected":
            self._end(session, "ended" if status == "completed" else "failed", status)
            await self._audit(session, "voice_call.ended", "success")

    async def accept_stream(self, ws: WebSocket, session_id: str) -> None:
        """Twilio's Media Stream for a call: authenticate, then run the conversation."""
        session = self.calls.get(session_id)
        if session is None or session.stream_used or session.state in TERMINAL_STATES:
            await ws.close(code=4404)
            return
        if not session.simulated and not await self._signed(ws):
            with contextlib.suppress(PlatformError):  # audited (rate-limited), then refused
                await self.telephony._reject("bad_stream_signature", f"{MEDIA_PATH}/{session_id}")
            await ws.close(code=4403)
            return
        await ws.accept()
        try:
            stream_sid = await self._handshake(ws, session)
        except (WebSocketDisconnect, ValueError, KeyError):
            await _close(ws, 4401)
            return
        if stream_sid is None:
            await _close(ws, 4401)
            return
        session.stream_used = True
        session.state = "connected"
        session.connected_at = utcnow()
        try:
            await Conversation(self, session, ws, stream_sid).run()
        except Exception as exc:
            log.exception("voice call %s failed", session.id)
            session.error = f"{type(exc).__name__}"
            self._end(session, "failed", "error")
        else:
            if session.state not in TERMINAL_STATES:
                self._end(session, "ended", session.end_reason or "caller_hung_up")
        finally:
            with contextlib.suppress(httpx.HTTPError):
                await self.gateway.delete(f"/leases/holders/manual/voice:{session.id}")
            await self._audit(session, "voice_call.ended", "success")
            await _close(ws, 1000)

    async def _signed(self, ws: WebSocket) -> bool:
        sent = ws.headers.get("x-twilio-signature", "")
        if not sent:
            return False
        creds = await self.telephony._signing_credentials()
        path = ws.url.path + (f"?{ws.url.query}" if ws.url.query else "")
        base = self.settings.twilio_base_url()
        # Twilio signs the stream URL as configured (wss://); accept the https:// spelling of
        # the same URL too.
        for origin in (base.replace("https://", "wss://", 1), base):
            if hmac.compare_digest(signature(creds["authToken"], origin + path, {}), sent):
                return True
        return False

    async def _handshake(self, ws: WebSocket, session: VoiceSession) -> str | None:
        """Wait for Twilio's ``start`` message and check the call's one-time token."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
            if message.get("event") != "start":
                continue
            start = message["start"]
            sent = str((start.get("customParameters") or {}).get("token") or "")
            if not hmac.compare_digest(sent.encode(), session.token.encode()):
                return None
            if session.twilio_sid is None:
                session.twilio_sid = start.get("callSid")
            return str(message.get("streamSid") or start.get("streamSid") or "")
        return None

    # --- Helpers --------------------------------------------------------------------------

    def _session(self, session_id: str) -> VoiceSession:
        session = self.calls.get(session_id)
        if session is None:
            raise not_found("Voice call", session_id)
        return session

    def _end(self, session: VoiceSession, state: str, reason: str) -> None:
        session.state = state
        session.end_reason = session.end_reason or reason
        session.ended_at = session.ended_at or utcnow()
        session.hangup.set()

    def _expire(self) -> None:
        now = utcnow()
        for sid, session in list(self.calls.items()):
            if session.ended_at and (now - session.ended_at).total_seconds() > SESSION_KEEP_SECONDS:
                del self.calls[sid]

    async def _audit(self, session: VoiceSession, action: str, outcome: str) -> None:
        async with self.db() as db:
            audit.record(
                db,
                action=action,
                actor_type="user",
                actor_id=session.user_id,
                target_type="voice_call",
                target_id=session.id,
                outcome=outcome,
                metadata={
                    "to": session.to_masked,
                    "state": session.state,
                    "endReason": session.end_reason,
                    "turns": sum(1 for t in session.turns if t.role == "caller"),
                    "durationSeconds": session.view()["durationSeconds"],
                    "simulated": session.simulated,
                },
            )
            await db.commit()


async def _close(ws: WebSocket, code: int) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code)


class Conversation:
    """One connected call: caller audio in, assistant audio out."""

    def __init__(
        self, service: VoiceService, session: VoiceSession, ws: WebSocket, stream_sid: str
    ) -> None:
        self.service = service
        self.settings = service.settings
        self.session = session
        self.ws = ws
        self.stream_sid = stream_sid
        self.vad = VoiceActivityDetector()
        self.utterances: asyncio.Queue[np.ndarray | None] = asyncio.Queue()
        self.reply: asyncio.Task[None] | None = None
        self.speaking = False  # assistant audio sent and not yet played out (mark pending)
        self.marks = 0
        self.barge = 0
        self.history: list[dict[str, str]] = []
        self.send_lock = asyncio.Lock()
        self.background: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        agent = asyncio.create_task(self._agent())
        timer = asyncio.create_task(self._time_limit())
        try:
            await self._receive()
        finally:
            self.session.hangup.set()
            await self.utterances.put(None)
            for task in (timer, self.reply, agent):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def _receive(self) -> None:
        hangup = asyncio.create_task(self.session.hangup.wait())
        try:
            while not self.session.hangup.is_set():
                receive = asyncio.create_task(self.ws.receive_text())
                done, _ = await asyncio.wait({receive, hangup}, return_when=asyncio.FIRST_COMPLETED)
                if receive not in done:
                    receive.cancel()
                    return
                message = json.loads(receive.result())
                event = message.get("event")
                if event == "media":
                    self._on_audio(ulaw_decode(base64.b64decode(message["media"]["payload"])))
                elif event == "mark":
                    self.marks -= 1
                    if self.marks <= 0:
                        self.marks = 0
                        self.speaking = False
                elif event == "stop":
                    self.session.end_reason = self.session.end_reason or "caller_hung_up"
                    return
        except WebSocketDisconnect:
            self.session.end_reason = self.session.end_reason or "caller_hung_up"
        finally:
            hangup.cancel()

    def _on_audio(self, samples: np.ndarray) -> None:
        for i in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
            frame = samples[i : i + FRAME_SAMPLES]
            if self.speaking:
                # Barge-in needs sustained loud speech: the line may echo the assistant.
                level = rms(frame)
                loud = level > max(1500.0, 5 * self.vad.noise_floor)
                self.barge = self.barge + 1 if loud else 0
                if self.barge * FRAME_MS >= self.settings.voice_barge_in_ms:
                    self.barge = 0
                    self._interrupt()
                    self.vad.reset()
                continue
            kind, utterance = self.vad.feed(frame)
            if kind == "end" and utterance is not None:
                self.utterances.put_nowait(utterance)

    def _interrupt(self) -> None:
        if self.reply is not None and not self.reply.done():
            self.reply.cancel()
        self.speaking = False
        self.marks = 0
        task = asyncio.create_task(self._send({"event": "clear", "streamSid": self.stream_sid}))
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def _time_limit(self) -> None:
        await asyncio.sleep(self.settings.voice_max_call_seconds)
        self.session.end_reason = "time_limit"
        if self.reply is not None and not self.reply.done():
            self.reply.cancel()
        await self._say(TIMEOUT_GOODBYE)
        await asyncio.sleep(len(TIMEOUT_GOODBYE.split()) * 0.4 + 1)
        self.session.hangup.set()

    async def _agent(self) -> None:
        self.reply = asyncio.create_task(self._say(GREETING))
        with contextlib.suppress(asyncio.CancelledError):
            await self.reply
        while True:
            utterance = await self.utterances.get()
            if utterance is None:
                return
            heard_at = time.perf_counter()
            text, asr_ms = await self._transcribe(utterance)
            if not text:
                continue
            self.session.turns.append(Turn("caller", text, utcnow(), timings={"asrMs": asr_ms}))
            self.reply = asyncio.create_task(self._answer(text, heard_at, asr_ms))
            with contextlib.suppress(asyncio.CancelledError):
                await self.reply

    async def _transcribe(self, samples: np.ndarray) -> tuple[str, int]:
        started = time.perf_counter()
        response = await self.service.gateway.post(
            "/audio/transcriptions",
            params={
                "modelId": self.settings.voice_asr_model,
                "holderId": self.session.id,
                "language": "en",
                "filename": "turn.wav",
            },
            content=wav_bytes(samples, TELEPHONE_RATE),
            headers={"Content-Type": "audio/wav"},
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            log.warning("transcription failed: %s", response.status_code)
            return "", elapsed
        return str(response.json().get("text") or "").strip(), elapsed

    def _messages(self, text: str) -> list[dict[str, str]]:
        system = SYSTEM_PROMPT
        if self.session.instructions:
            system += "\n\nThe owner's instructions for this call:\n" + self.session.instructions
        self.history.append({"role": "user", "content": text})
        self.history = self.history[-MAX_HISTORY_TURNS:]
        return [{"role": "system", "content": system}, *self.history]

    async def _llm_words(
        self, text: str, timings: dict[str, int], started: float
    ) -> AsyncIterator[str]:
        body = {
            "profile": self.settings.voice_llm_profile,
            "messages": self._messages(text),
            "maxOutputTokens": 160,
            "temperature": 0.6,
            "stream": True,
            "holder": {"type": "voice", "id": self.session.id},
        }
        async with self.service.gateway.stream("POST", "/llm/chat", json=body) as response:
            if response.status_code >= 400:
                await response.aread()
                raise PlatformError("MODEL_ERROR", f"chat failed ({response.status_code})", 502)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("type") == "delta" and event.get("text"):
                    timings.setdefault(
                        "llmFirstTokenMs", int((time.perf_counter() - started) * 1000)
                    )
                    yield str(event["text"])
                elif event.get("type") == "error":
                    raise PlatformError("MODEL_ERROR", str(event.get("error")), 502)

    async def _answer(self, text: str, heard_at: float, asr_ms: int) -> None:
        timings: dict[str, int] = {"asrMs": asr_ms}
        spoken: list[str] = []
        turn = Turn("assistant", "", utcnow(), timings=timings)
        self.session.turns.append(turn)
        try:
            await self._speak(self._llm_words(text, timings, heard_at), spoken, timings, heard_at)
        except asyncio.CancelledError:
            turn.interrupted = True
            raise
        finally:
            turn.text = " ".join(spoken).strip()
            if turn.text:
                self.history.append({"role": "assistant", "content": turn.text})

    async def _say(self, text: str) -> None:
        async def words() -> AsyncIterator[str]:
            yield text

        spoken: list[str] = []
        turn = Turn("assistant", text, utcnow())
        self.session.turns.append(turn)
        try:
            await self._speak(words(), spoken, turn.timings, time.perf_counter())
        except asyncio.CancelledError:
            turn.interrupted = True
            raise

    async def _speak(
        self,
        text: AsyncIterator[str],
        spoken: list[str],
        timings: dict[str, int],
        started: float,
    ) -> None:
        """Stream text into the speech model and its audio to Twilio as it is produced."""
        query = (
            f"modelId={self.settings.voice_tts_model}&voice={self.session.voice}"
            f"&holderId={self.session.id}"
        )
        url = f"{self.service.gateway_ws}/internal/v1/audio/speech/stream?{query}"
        async with websockets.connect(
            url, additional_headers=self.service.gateway_headers, max_size=1 << 20
        ) as tts:

            async def send(words: list[str]) -> None:
                for word in words:
                    clean = speakable(word)
                    if clean:
                        spoken.append(clean)
                        await tts.send(json.dumps({"type": "text", "text": clean}))

            async def feed() -> None:
                pending = ""
                async for chunk in text:
                    pending += chunk
                    complete, pending = split_words(pending)
                    await send(complete)
                await send(pending.split())
                await tts.send(json.dumps({"type": "end"}))

            feeder = asyncio.create_task(feed())
            down = Downsampler()
            try:
                async for message in tts:
                    if isinstance(message, bytes):
                        audio = ulaw_encode(down.feed(message))
                        if not audio:
                            continue
                        if "firstAudioMs" not in timings:
                            timings["firstAudioMs"] = int((time.perf_counter() - started) * 1000)
                        self.speaking = True
                        await self._send(
                            {
                                "event": "media",
                                "streamSid": self.stream_sid,
                                "media": {"payload": base64.b64encode(audio).decode()},
                            }
                        )
                        continue
                    event = json.loads(message)
                    if event.get("type") == "error":
                        raise PlatformError("MODEL_ERROR", str(event.get("error")), 502)
                    if event.get("type") == "done":
                        break
                await feeder
                self.marks += 1
                await self._send(
                    {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": "reply"}}
                )
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await tts.send(json.dumps({"type": "cancel"}))
                raise
            finally:
                feeder.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await feeder

    async def _send(self, message: dict[str, Any]) -> None:
        async with self.send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(message))
