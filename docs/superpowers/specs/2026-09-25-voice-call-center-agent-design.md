# Voice call center agent: design

- Date: 2026-09-25
- Author: Person 5 (Vineet Kumar)
- Branch: `feature/voice-call-center-agent` (from `feature/person5-sdk-agents-qa`)
- Reference module: `truestar-voice` at `4c34cce` (LiveKit Agents campaign-call worker)

## 1. Goal

A Crewquarters marketplace agent that holds real outbound phone conversations. The owner installs it,
points it at a Google Sheet of consenting contacts and writes a call brief. The owner approves the
call plan, and the agent calls each contact and has a short, natural conversation about the brief.
It writes the outcome of each call back to the sheet.

Everything runs locally: the LiveKit media server, its SIP gateway, speech-to-text, the LLM,
text-to-speech, and the agent. The only thing that leaves the building is the phone leg, through
the owner's SIP trunk provider.

### Decisions taken with the owner

| Question | Answer |
| --- | --- |
| Call direction | Outbound only |
| Purpose | A Crewquarters agent (not TrueStar campaigns) |
| Telephony | Self-hosted LiveKit (server + SIP) on the appliance; developed locally first |
| Models | Not chosen; recommend a set that runs on the GB10 (arm64, NVIDIA) |
| Languages | English only |
| Architecture | A: platform voice stack, with the agent holding the conversation (not a separate voice service, and no sandbox exceptions) |

### Out of scope

Inbound calls, call transfer to a human, languages other than English, concurrent calls within one
run, call recording storage, and SMS.

## 2. Ethics and consent (non-negotiable)

- **The agent never claims to be human.** The reference prompt tells the model to deny being an AI.
  That rule is removed. The greeting says the call is automated and on whose behalf it is made, and
  a direct question gets an honest answer. This follows `PLAN.md` §12 (disclosure first) and the
  AI-voice rules for outbound calls (TCPA consent for artificial voices; state bot-disclosure laws).
- Only rows with recorded consent are called, reusing the caller agent's consent rules
  (`caller_agent.rows.classify`). The owner approves the exact recipients, brief, and disclosure
  before any dialing (`ctx.input.ask`, key hashed over all three).
- "Stop calling me" or "not interested" ends the call politely and marks the row `dnc` or
  `declined`. The agent never pressures, never asks for payment or identity numbers, and never
  leaves a voicemail.
- A per-call time cap, a per-run call cap, and ring timeouts bound what one run can do.

## 3. Architecture

```
                         GB10 appliance (or laptop in development)
  ┌───────────────────────────────────────────────────────────────────────────────┐
  │  voice-call-center agent container (hardened, per run, network: crewq-agents)   │
  │    LiveKit AgentSession: VAD → STT → turn detector → LLM → TTS                 │
  │      │ OpenAI-compatible calls (bearer = run token)       │ WebRTC media       │
  │      ▼                                                    ▼                    │
  │  capability broker ──► model gateway ──┬─► vLLM (local.general.*)   LiveKit    │
  │   /openai/v1/*          checks         ├─► speech server            server ◄──┐│
  │   /voice/calls          llm.profile:*  │    (local.stt.*, local.tts.*)        ││
  │      │                                 │                                      ││
  │      └── LiveKit server API: create room, mint room token, dial via SIP ──────┘│
  │                                                          LiveKit SIP ──────────┼──► SIP trunk ──► phone
  └───────────────────────────────────────────────────────────────────────────────┘
```

- The agent holds the conversation. It is a port of the reference campaign agent's session logic:
  greeting timing, voicemail/IVR detection, texture, `end_call`, and hang-up grace.
- The agent never holds LiveKit API keys, SIP trunk credentials, or model endpoints. The broker
  dials and returns a short-lived **room token** for one room. Model calls go through the broker's
  OpenAI-compatible facade with the run token as the API key, where each model name is a bound
  profile variant checked against `llm.profile:<variant>`.
- The agent container's network gains exactly one destination: the local LiveKit server, for media.

## 4. Contract changes

These are additive. The canonical files belong to other owners and are flagged for their review in
`docs/decisions/0001-person5-contract-drafts.md` (D22–D25).

| Change | File(s) | Owner |
| --- | --- | --- |
| Model profile families `local.stt` → `local.stt.small`, `local.tts` → `local.tts.small` | `crewquarters_shared/manifest.py` (`DEFAULT_VARIANTS`, `KNOWN_VARIANTS`) | Persons 1, 2 |
| Permission `connectors.sip: ["call.conversational"]` → capability `sip.call.conversational` | `agent-manifest.schema.json`, `capabilities.yaml`, `crewquarters_shared/capability.py` | Persons 1, 3 |
| Broker: `POST /voice/calls`, `GET /voice/calls/{id}`, `POST /voice/calls/{id}/hangup` | `broker-sdk.openapi.yaml` (draft) | Person 3 |
| Broker: OpenAI-compatible facade `POST /openai/v1/chat/completions`, `/audio/transcriptions`, `/audio/speech`. Tool calls are allowed on this facade only; `/llm/chat` keeps D13. | `broker-sdk.openapi.yaml` (draft) | Persons 2, 3 |

The speech profiles live in `llmProfiles` like `local.embedding` already does: the list names
model profiles, not only LLMs. `sip` is absent from existing manifests and is treated as `[]`, so
existing installs and approvals are unchanged.

### `VoiceCall`

`{id, idempotencyKey, toMasked, state, room: {url, name, token, identity} | null, calleeIdentity,
answeredAt, endedAt, durationSeconds, errorCode, createdAt, updatedAt}`.

The state is one of `dialing`, `ringing`, `answered`, `completed`, `busy`, `no-answer`, `failed`,
or `canceled`. Dialing is idempotent by `idempotencyKey`. The room token is issued for the agent
identity `agent`. It only grants joining that room, publishing, and subscribing, and expires
after `ringTimeoutSeconds + maxDurationSeconds + 60`.

## 5. Models

| Profile | Default variant | Engine (laptop CPU and GB10) | Why |
| --- | --- | --- | --- |
| `local.stt.small` | NVIDIA Parakeet TDT 0.6B v2, int8 (482 MB) | sherpa-onnx offline transducer | Best English accuracy in its class; fast offline decode per utterance; arm64 wheels. NVIDIA Nemotron streaming 0.6B is the next step for true streaming STT (§11). |
| `local.tts.small` | Kokoro 82M v0.19, fp32 (320 MB), voice `af_sarah` | sherpa-onnx | Natural English; same engine as STT. fp32, not int8: int8 is 2.4× slower on ARM CPUs (measured) |
| `local.general.small` | Chosen by Person 2's model catalog (vLLM). For voice, prefer a small or mixture-of-experts model with a fast first token. | vLLM | The LLM dominated latency (~750 ms of ~1.2 s) in NVIDIA's measured all-local voice agent on DGX Spark |

Measured on an Apple M3 Pro CPU (4 threads, sherpa-onnx 1.13.8) on 2026-09-25:
- Parakeet transcribed 6.0 s of speech in 0.15 s (RTF 0.025), exact apart from one name.
- Kokoro fp32 synthesizes at RTF 0.24 (int8: 0.58). A short clause takes about 0.5 s.

So the speech server synthesizes **clause by clause** (splitting at commas and sentence ends,
at least three words per clause) and streams each clause as soon as it is ready. The first audio
comes out in about 0.5 s, and later clauses stay ahead of playback.

The **speech server** (`packages/speech_server`, `crewquarters-speech`) serves the OpenAI-compatible
`POST /v1/audio/transcriptions` and `POST /v1/audio/speech` endpoints (streamed 24 kHz s16le PCM).
It has two engines:
- `sherpa`: the models above, downloaded ahead of time by `crewq-speech download`, never at
  request time.
- `fake`: deterministic, with no models, for tests.

The model gateway, stood in for by the fake platform, routes `local.stt.*` and `local.tts.*` to it.

## 6. The call, step by step

1. The agent reads the sheet. `classify` picks consenting, ready rows with valid E.164 numbers, up
   to `maxCalls`.
2. **Approval card:** masked recipients, the brief, the disclosure, and the per-call time cap.
   Nothing is dialed without `approve`.
3. For each approved row, `ctx.idempotency.once("voice:<run>:<row>", dial)` calls
   `ctx.voice.dial(to, idempotency_key=…)`. A retried run never redials a row whose call was
   created.
4. The agent joins the room with the returned token and waits for the callee participant. The
   wait is bounded by the ring timeout, and the call state is polled for `busy`, `no-answer`, or
   `failed`.
5. **Session:** `AgentSession` runs with:
   - `stt`: `openai.STT(use_realtime=False)` through the facade;
   - `llm`: `openai.LLM`;
   - `tts`: `openai.TTS(response_format="pcm")`;
   - Silero VAD;
   - LiveKit's multilingual turn-detector model, run **in process** through a custom inference
     executor, so there is no LiveKit job and no LiveKit secrets in the container.

   All three model names are profile variants resolved from the grants.
6. **Conversation:**
   - Pickup wait (5 s); if the line is silent, a "Hello?" probe; then the greeting with disclosure
     and a time check.
   - Short turns under the brief. Texture adds openers only; bracket tags are stripped, because
     Kokoro does not perform them.
   - The voicemail/IVR detector hangs up without leaving a message.
   - `end_call` tool, with a 1 s hang-up grace; hard cap at `maxCallSeconds`.
7. **Outcome:** after the call, one structured LLM call turns the transcript into
   `{disposition, interest, callbackRequested, followUp, notes}`. The disposition is one of
   `completed`, `declined`, `dnc`, `callback`, `voicemail`, `no_answer`, `busy`, `failed`, or
   `wrong_person`.
8. **Results:** the row is written to `Results!A<row>:J<row>` with the same fixed-range update as
   the caller agent (idempotent). Events report progress per call and latency metrics, and the
   transcript summary goes out as an artifact event. Transcripts are never logged in full, and
   phone numbers are always masked.

## 7. Agent package (`agents/voice_caller`, id `voice-call-center`)

| Module | Source |
| --- | --- |
| `voicemail.py` | Ported verbatim (pure, tested) |
| `texture.py` | Ported; sound injection off unless the voice is expressive |
| `safety.py`, `metrics.py` (from `call_metrics.py`), `transcript.py` | Ported |
| `prompts.py` | Rewritten from `build_campaign_system_prompt`: keeps the brevity rules, ear-first writing, one question at a time, English only, and the ending rules. Removes AI-denial and TrueStar specifics. Adds disclosure, honesty, DNC handling, and brief-driven topics. |
| `turn_detection.py` | New: in-process inference executor for LiveKit's EOU model |
| `session.py` | Ported lifecycle from `campaign_agent.entrypoint`: participant wait, pickup wait and probe, voicemail poll, `end_call`, finalize |
| `outcome.py`, `results.py`, `approval.py`, `config.py`, `agent.py`, `__main__.py` | New, following the caller agent's patterns (reuses `caller_agent.rows`) |

The manifest requests:
- `llmProfiles: [local.general, local.stt, local.tts]`;
- `connectors: {google: [spreadsheets], sip: [call.conversational]}`;
- `userInput: true`;
- resources: 2 CPU, 2048 MB, `activeTimeoutSeconds` 7200.

The result schema carries `x-crewquarters-renderer: crewquarters.voice-call-center/v1`. The image
bakes in the VAD and turn-detector weights and sets `HF_HUB_OFFLINE=1`.

## 8. SDK additions

- `ctx.voice`: `dial(to, *, idempotency_key, ring_timeout_seconds=30, max_duration_seconds=300)`,
  `get(call_id)`, `hangup(call_id)`, returning `VoiceCall`. Dialing is retried only with its
  idempotency key, like `create_call`.
- `ctx.model_endpoint()` returns `ModelEndpoint(base_url, api_key)` for OpenAI-compatible clients,
  pointed at the broker facade with the run token.

## 9. Fake platform (development and tests)

- `/openai/v1/*` facade:
  - chat completions (JSON and SSE, with tool calls from mock rules or passed to an
    OpenAI-compatible server);
  - transcriptions and speech, forwarded to `CREWQ_FAKE_SPEECH_URL` or to the built-in fake speech
    engine.
- `/voice/calls` sits behind a `VoiceBackend`:
  - `LiveKitVoiceBackend` (livekit-api: rooms, tokens, SIP dial when a trunk id is set) with a
    **simulated callee**: a LiveKit participant that follows the scenario, speaks its lines, and
    can be busy, not answer, or play a voicemail greeting;
  - `OfflineVoiceBackend` for unit tests.
- With the fake speech engine, the simulated callee registers each line before speaking it, and
  fake STT returns registered lines in order. The whole call loop then runs through a real local
  LiveKit server without any models.

## 10. Testing

| Layer | What | Runs in |
| --- | --- | --- |
| Unit | Ported voicemail, texture, safety, and metrics tests; prompts (disclosure present, no AI denial), config, approval, outcome parsing, turn-detector executor | `make test-sdk`, CI |
| Conversation | `AgentSession.run(user_input=…)` in text mode against the fake facade and mock rules: greeting flow, "not interested" → `end_call`, "are you a robot?" → honest answer | CI |
| Fake platform | Facade (streaming, tools, speech), voice routes, capability checks, audit | CI |
| Contract | Canonical additions, broker draft parity and traffic, manifests | CI |
| Speech server | API with the fake engine; sherpa engine behind marker `models` (downloads models) | CI / opt-in |
| Voice E2E | Real local LiveKit (Docker), simulated callee, agent process, fake speech (`-m voice`); the same with the real speech server (`-m "voice and models"`) | laptop / CI job |

## 11. Deployment

- **Laptop:** `make voice-up` starts LiveKit (dev keys, single-port UDP/TCP, `node_ip` 127.0.0.1) and
  the speech server. `make voice-demo` runs a simulated call end to end.
- **GB10:**
  - the same services under the Compose profile `voice`, plus `livekit-sip` and Redis for the
    trunk;
  - LiveKit's `node_ip` is set to the LAN address, and SIP/RTP ports are opened to the trunk
    provider;
  - vLLM serves the LLM profile.
- Speech runs on the GB10's CPU at first. GPU speech is a follow-up: sherpa-onnx CUDA on CUDA 13,
  or NVIDIA's DGX Spark-supported Parakeet NIM.

## 12. Known limitations (stated, not hidden)

- Speech-to-text is per utterance after VAD, not streaming, which adds roughly 0.2–0.5 s per turn.
  The next step is streaming Nemotron through a WebSocket STT plugin.
- Kokoro is not expressive like Inworld: no sounds or delivery directions, only natural prosody.
- Not verified on GB10 hardware or with a live SIP trunk in this work; the laptop E2E covers the
  whole loop with a simulated callee.
- The platform's real broker and model gateway do not exist yet. The fake stands in, as for the
  other agents, and the contract drafts describe what they must do.
