# Voice call center agent and the local voice stack

`voice-call-center` (`agents/voice_caller`) holds short, natural outbound phone conversations for
the owner. It reads consenting contacts from a Google Sheet, asks the owner to approve the call
plan, calls each contact, and writes the outcome of every call back to the sheet.

Everything runs on the appliance: the LiveKit media server and its SIP gateway, speech-to-text,
the LLM, text-to-speech, and the agent. Only the phone leg leaves the building, through the
owner's SIP trunk provider.

The design is in [the spec](../superpowers/specs/2026-09-25-voice-call-center-agent-design.md).
The conversation logic is ported from the `truestar-voice` campaign agent (provenance headers in
each ported module).

## Honesty and consent

These rules are enforced in code, not left to configuration:

- **The agent never claims to be human.** The greeting's first sentence says the call is from an
  automated assistant and on whose behalf it is made. `disclosure` must contain words such as
  "automated", "AI", "virtual assistant", or "bot"; any other value is rejected as
  `CONFIG_INVALID`. A direct "are you a robot?" gets an honest answer.
- Only rows with recorded consent are called (the caller agent's rules). The owner approves the
  exact recipients, brief, disclosure, and limits before anything is dialed. The approval key is
  a hash over all of them, so a changed plan asks again.
- "Stop calling" ends the call politely, records `dnc`, and warns the owner to mark the contact
  `dnc` (agents cannot edit the Contacts tab). The agent never leaves a
  voicemail, never asks for payment or identity numbers, and never pressures.
- A per-call time cap (`maxCallSeconds`), a per-run cap (`maxCalls`, at most 25), and ring
  timeouts bound what one run can do. A retried run never redials a row it already dialed.

Calling real people also needs the owner's legal review of the calling, recording, AI-voice
consent, and do-not-call rules that apply to them.

## Architecture

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

- The agent holds no LiveKit keys, SIP credentials, or model endpoints. The broker dials and
  returns a token for one room (`ctx.voice.dial`). Model calls go through the broker's
  OpenAI-compatible facade (`ctx.model_endpoint()`), with the run token as the API key. There,
  every model name is a granted profile variant checked against `llm.profile:<variant>`.
- The agent container's only new destination is the local LiveKit server, for media.
- End-of-turn detection runs LiveKit's turn-detector model in process, so there is no LiveKit
  job and no LiveKit secret in the container. The VAD and turn-detector weights are baked into the
  image (`HF_HUB_OFFLINE=1`).
- Until the real broker and model gateway exist, the fake platform stands in for both. Its
  behaviour is the draft `packages/contracts/broker-sdk.voice.openapi.yaml`, which adds voice
  calls and the model facade to the stable `broker-sdk.openapi.yaml`.

## Models

| Profile | Default | Engine | Why |
| --- | --- | --- | --- |
| `local.stt.small` | NVIDIA Parakeet TDT 0.6B v2, int8 (482 MB) | sherpa-onnx offline transducer | Best English accuracy in its class; fast per-utterance decode; arm64 wheels |
| `local.tts.small` | Kokoro 82M v0.19, fp32 (320 MB), voice `af_sarah` | sherpa-onnx | Natural English. fp32 because int8 is 2.4× slower on ARM CPUs |
| `local.general.small` | Person 2's model catalog (vLLM) | vLLM | The LLM's first token dominates the reply delay; prefer a small or mixture-of-experts instruct model |

The speech server (`packages/speech_server`, `crewq-speech`) serves the OpenAI-compatible
`/v1/audio/transcriptions` and `/v1/audio/speech` endpoints. It synthesizes clause by clause and
streams each clause as soon as it is ready. `crewq-speech download` fetches the models ahead of
time and checks their pinned SHA-256; nothing is downloaded at request time.

Measured on an Apple M3 Pro CPU (4 threads) on 2026-09-25:

| Measurement | Result |
| --- | --- |
| Parakeet, 6 s of speech | 0.15 s (real-time factor 0.025) |
| Kokoro fp32 / int8 | real-time factor 0.24 / 0.58; a short clause takes about 0.5 s |
| Turn detector | 15 ms per prediction after a 2 s load |
| Silence after the callee stops talking, before the agent's reply, over WebRTC through LiveKit | 0.63–0.71 s with fake speech; 1.45–1.9 s with Parakeet and Kokoro (mock LLM) |

**The LLM through vLLM.** The agent ends calls with the `end_call` tool, so the model server must
accept tools: start vLLM with `--enable-auto-tool-choice` and the `--tool-call-parser` for the
chosen model family. For a pilot on the fake platform, point it at vLLM with
`CREWQ_FAKE_LLM_BASE_URL` and `CREWQ_FAKE_LLM_MODEL`; chat completions, including tool calls, are
passed through.

## Laptop quickstart

Docker and `uv` are all you need; no models are needed for the first three commands.

```bash
make sync
make livekit-up             # LiveKit on ws://127.0.0.1:7880 (development key)
make voice-e2e              # the agent process holds three calls against a simulated callee (~1 min)
make voice-e2e-containers   # the same with the agent in its hardened container (builds its image)
make voice-up               # downloads the speech models once (~800 MB), starts the speech server
make voice-e2e-models       # the same calls with Parakeet and Kokoro
make voice-down
```

The E2E scenario is `tests/fixtures/scenarios/voice-e2e`. One contact answers and confirms an
appointment, one reaches voicemail, and one is busy. The simulated callee is a LiveKit participant
that speaks its script with real synthesized audio, so voice-activity and end-of-turn detection
run on real audio even with fake speech. Set `LOG_LEVEL=INFO` for the agent to see each call's
progress, and `CREWQ_SPEECH_THREADS` to change the speech server's threads.

## Configuration

| Field | Default | Notes |
| --- | --- | --- |
| `spreadsheetId` | required | The sheet with the contacts |
| `inputRange` / `resultRange` | `Contacts!A2:D` / `Results!A:J` | Columns `name, phone_e164, consent, status` |
| `organization`, `purpose` | required | Who the call is for, and what it is about (the brief) |
| `talkingPoints`, `questions` | `[]` | At most 10 and 8 short items |
| `agentName` | `Sam` | The name the agent introduces itself with |
| `disclosure` | `an automated AI assistant` | Must say the caller is automated (see above) |
| `maxCalls` | 5 | 1–25 per run |
| `maxCallSeconds`, `ringTimeoutSeconds` | 240, 30 | 60–900 and 10–60 |
| `voice` | `af_sarah` | A Kokoro v0.19 voice |
| `modelProfile`, `sttProfile`, `ttsProfile` | `local.general.small`, `local.stt.small`, `local.tts.small` | Must be granted to the installation |

Each call writes `Results!A<row>:J<row>` (the source row, so a retried write never duplicates):
`source_row, name, phone_masked, call_id, disposition, interest, callback, notes,
duration_seconds, completed_at`.

The disposition is one of `completed`, `declined`, `dnc`, `callback`, `voicemail`, `no_answer`,
`busy`, `failed`, or `wrong_person`. Agents may write only within the configured `resultRange`,
so the agent never edits the Contacts tab. When someone asks not to be called again, the run logs
a warning naming the row: set that contact's `status` to `dnc` (and reached contacts to `called`)
before the next run, which then skips them.

## Deploying on the GB10

### LiveKit modes

LiveKit must advertise a media address that the agents can reach. `LIVEKIT_MODE` (or
`CREWQ_LIVEKIT_MODE` for Compose) picks `infra/livekit/livekit.<mode>.yaml`:

| Mode | `node_ip` | Use |
| --- | --- | --- |
| `host` | 127.0.0.1 | Agents as local processes (laptop development) |
| `containers` | 10.231.72.10, LiveKit's fixed address on `crewq-agents` | Hardened agent containers: the appliance |
| `sip` | 10.231.72.10, plus Redis | Containers mode with LiveKit SIP for real phone calls |

The agent network `crewq-agents` is internal (no route out) with the subnet 10.231.72.0/24, so
the address is stable. On the appliance: `make voice-up LIVEKIT_MODE=containers` (or `sip`).

### Replace the development key

`infra/livekit/*.yaml` ship with the key `crewq-dev` and a published secret. They are for a
laptop only. Before the appliance joins a network:

1. Generate a secret of at least 32 random characters (`openssl rand -base64 36`).
2. Put the new key and secret in `infra/livekit/livekit.<mode>.yaml` (`keys:`) and
   `infra/livekit/sip.yaml` (`api_key`, `api_secret`), from a file outside the repository.
3. Give the broker the same key: for the fake platform, `CREWQ_FAKE_LIVEKIT_API_KEY` and
   `CREWQ_FAKE_LIVEKIT_API_SECRET`.

The LiveKit ports are published on 127.0.0.1 only. Agents reach LiveKit on the internal network,
never through the host.

### Real phone calls through a SIP trunk

1. Get a SIP trunk and a number from a provider that supports outbound calling to your
   destinations, and verify the caller ID it requires.
2. Start the SIP profile:

   ```bash
   make voice-up LIVEKIT_MODE=sip
   docker compose -f infra/compose/compose.yaml --profile sip up -d redis livekit-sip
   ```

3. Open UDP 5060 (SIP) and UDP 10000–10100 (RTP) on the router to the GB10, **only from the
   provider's published signalling and media addresses**. livekit-sip finds its public address
   through STUN (`use_external_ip: true`).
4. Register the outbound trunk with LiveKit (the `lk` CLI, pointed at `http://127.0.0.1:7880`
   with the key above):

   ```json
   {
     "trunk": {
       "name": "crewquarters-outbound",
       "address": "<provider SIP domain>",
       "numbers": ["<your E.164 caller ID>"],
       "authUsername": "<from the provider>",
       "authPassword": "<from the provider>"
     }
   }
   ```

   `lk sip outbound create outbound-trunk.json` prints the trunk id (`ST_…`). Keep this file out
   of the repository: it holds the provider credentials.
5. Give the broker the trunk id: for the fake platform, `CREWQ_FAKE_SIP_TRUNK_ID`. With a trunk
   id set, calls dial the real number instead of the simulated callee. SIP 486/600 map to `busy`,
   480/408 to `no-answer`, and anything else to `failed` with `SIP_<code>`.

Place the first calls to your own test numbers.

### Speech on the GB10

The speech server runs on the Grace CPU (arm64 wheels, `CREWQ_SPEECH_THREADS`, default 4), and
leaves the GPU to vLLM. Moving speech to the GPU is a follow-up. The options are sherpa-onnx with
CUDA on the GB10's CUDA 13 stack, or NVIDIA's Parakeet NIM where DGX Spark supports it.

## Known limitations

- Speech-to-text runs per utterance after the VAD, not streaming, which adds about 0.2–0.5 s per
  turn. The next step is a streaming STT model (for example NVIDIA Nemotron streaming) behind a
  WebSocket STT plugin.
- Kokoro gives natural prosody, but not the sounds or delivery directions of the reference
  module's Inworld voices.
- Not yet verified on GB10 hardware or with a live SIP trunk. The laptop E2E covers the whole loop
  with a simulated callee, both as a process and in the hardened container.
- The real broker and model gateway do not exist yet. The fake platform stands in for both, and
  the contract drafts ([D22–D25](../decisions/0001-person5-contract-drafts.md)) describe what they
  must do.
- One call at a time per run; no inbound calls, transfers, recordings, or languages other than
  English.
