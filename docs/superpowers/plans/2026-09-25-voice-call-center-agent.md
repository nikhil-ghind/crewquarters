# Voice Call Center Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Crewquarters agent that holds real outbound phone conversations through a local LiveKit
server with local speech-to-text, LLM, and text-to-speech models, built on the `truestar-voice`
campaign agent.

**Architecture:** The platform owns telephony and models. The broker dials through LiveKit and
returns a room token. It also exposes an OpenAI-compatible model facade checked against profile
capabilities, and a new speech server serves `local.stt.*` and `local.tts.*`. The agent runs a
LiveKit `AgentSession` in its hardened container and reaches only the broker and the LiveKit
server. The fake platform, fake speech engine, and a simulated callee make the whole loop testable
on a laptop and in CI.

**Tech Stack:** Python 3.12, livekit-agents 1.8.3 (+ openai, silero, turn-detector plugins),
livekit 1.1.20 (rtc), livekit-api 1.2.1, sherpa-onnx 1.13.8 (Parakeet TDT 0.6B v2 int8, Kokoro
fp32 v0.19), FastAPI, LiveKit server v1.13.7, LiveKit SIP v1.17.0.

**Spec:** `docs/superpowers/specs/2026-09-25-voice-call-center-agent-design.md`

## Global Constraints

- Python `>=3.12,<3.13`; the lock is regenerated with `uvx --from uv==0.5.11 uv lock`.
- Exact pins: `livekit-agents[openai,silero,turn-detector]==1.8.3`, `livekit==1.1.20`,
  `livekit-api==1.2.1`, `sherpa-onnx==1.13.8`.
- ruff line length 100, rules `E,F,I,B,UP,ASYNC,S,SIM,RUF`; mypy strict over every `src` tree
  (`MYPY_PATHS` in the Makefile).
- The agent never claims to be human; the greeting discloses an automated call on the owner's
  behalf.
- Phone numbers are masked (`••••0101`) everywhere except the broker's dial request. Transcripts
  are not logged; only per-row summaries go to the sheet.
- The agent image downloads nothing at runtime (`HF_HUB_OFFLINE=1`; the VAD and turn-detector
  weights are baked in at build).
- No LiveKit API key, SIP credential, or model endpoint reaches the agent; only a room token and
  the run token.
- New canonical-contract changes are additive and listed as D22–D25 in
  `docs/decisions/0001-person5-contract-drafts.md`.
- Tests that need Docker or downloaded models carry the markers `voice` / `models` and are
  excluded from `make test-sdk`.

## Review Focus

1. The callee never answers, or the LiveKit connection drops mid-call. The agent records
   `no_answer`/`failed`, hangs up, and moves to the next row; the run never hangs.
   → Task 8 `test_unanswered_call_is_recorded_and_the_run_continues`.
2. A model call fails mid-conversation (STT or TTS 5xx). The call ends with disposition `failed`,
   and the run continues. → Task 8 `test_model_failure_fails_only_that_call`.
3. The run is retried after a crash mid-call. A row whose call was already created is never
   redialed (`in_doubt` → recorded as `failed` with `OUTCOME_UNKNOWN`).
   → Task 8 `test_retried_run_never_redials_a_row`.
4. The callee says "don't call me again". Disposition `dnc`, the row status is written `dnc`,
   and later runs skip it. → Task 7 `test_do_not_call_maps_to_dnc` and Task 8
   `test_dnc_row_is_marked_and_skipped_next_run`.
5. A voicemail greeting (long monologue) or silence. The agent hangs up without speaking a
   message, with disposition `voicemail`. → Task 7 ported voicemail tests and Task 9 E2E
   `voicemail` callee.

---

### Task 1: Canonical contract additions (speech profiles, SIP permission)

**Files:**
- Modify: `packages/shared_python/src/crewquarters_shared/manifest.py` (`DEFAULT_VARIANTS`, `KNOWN_VARIANTS`)
- Modify: `packages/shared_python/src/crewquarters_shared/capability.py` (`capabilities_from_permissions`)
- Modify: `packages/contracts/agent-manifest.schema.json` (`$defs.permissions.properties.connectors`)
- Modify: `packages/contracts/capabilities.yaml`
- Test: `packages/shared_python/tests/test_voice_contract.py` (new, `no_db`)

**Interfaces:**
- Produces: profiles `local.stt` → `local.stt.small`, `local.tts` → `local.tts.small`; capability `sip.call.conversational` from `permissions.connectors.sip == ["call.conversational"]`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from crewquarters_shared import capability, manifest

pytestmark = pytest.mark.no_db


def test_speech_families_resolve_to_default_variants() -> None:
    bindings = manifest.resolve_model_bindings(["local.stt", "local.tts", "local.general"])
    assert bindings == {
        "local.stt": "local.stt.small",
        "local.tts": "local.tts.small",
        "local.general": "local.general.small",
    }


def test_sip_connector_grants_conversational_calls() -> None:
    permissions = {
        "llmProfiles": ["local.stt"],
        "knowledge": [],
        "connectors": {"sip": ["call.conversational"]},
        "cloudProviders": [],
        "userInput": True,
    }
    caps = capability.capabilities_from_permissions(permissions, {"local.stt": "local.stt.small"})
    assert "sip.call.conversational" in caps
    assert "llm.profile:local.stt.small" in caps


def test_manifests_without_sip_still_validate_and_normalize_unchanged() -> None:
    ...  # load catalog/dev/hello-crew.yaml, validate, assert "sip" not added to connectors
```

- [ ] **Step 2:** Run `uv run pytest -q packages/shared_python/tests/test_voice_contract.py`. Expected: FAIL (`UNKNOWN_MODEL_PROFILE`; no `sip` capability).
- [ ] **Step 3:** Implement:
  - add `"local.stt": "local.stt.small", "local.tts": "local.tts.small"` to `DEFAULT_VARIANTS` and both variants to `KNOWN_VARIANTS`;
  - in `capabilities_from_permissions` add `for op in connectors.get("sip", []): caps.add(f"sip.{op}")`;
  - schema: `"sip": {"type": "array", "uniqueItems": true, "items": {"type": "string", "enum": ["call.conversational"]}}`;
  - `capabilities.yaml`: pattern `sip.call.conversational`, from `permissions.connectors.sip`, enforcedBy `capability_broker`, ui "Hold live phone conversations through your SIP trunk".
- [ ] **Step 4:** Run the new tests plus `uv run pytest -q tests/contract/test_contracts.py` (with `make db-up`). Expected: PASS, no OpenAPI drift.
- [ ] **Step 5:** Commit "Contracts: speech model profiles and the SIP conversational-call permission".

### Task 2: Broker draft: voice calls and the OpenAI-compatible model facade

**Files:**
- Modify: `packages/contracts/broker-sdk.openapi.yaml`
- Modify: `tests/contract/test_broker_contract_files.py` (vocabulary, operation expectations)

**Interfaces (paths under `/internal/v1/sdk`):**
- `POST /voice/calls` `VoiceCallCreate{to, idempotencyKey, ringTimeoutSeconds=30 (5–120), maxDurationSeconds=300 (30–1800)}` → `VoiceCall`; x-capability `sip.call.conversational`; x-idempotent `with-idempotency-key`.
- `GET /voice/calls/{id}` → `VoiceCall`; `POST /voice/calls/{id}/hangup` → `VoiceCall`.
- `VoiceCall{id, idempotencyKey, toMasked, state, room: VoiceRoom|null, calleeIdentity, answeredAt, endedAt, durationSeconds, errorCode, createdAt, updatedAt}`; `VoiceRoom{url, name, token, identity}`; the state enum is as in spec §4.
- `POST /openai/v1/chat/completions`, `POST /openai/v1/audio/transcriptions` (multipart), `POST /openai/v1/audio/speech`; x-capability `llm.profile:<variant> (and cloud.<provider> for cloud profiles)`. The schemas are the OpenAI subsets used by the LiveKit plugins (documented in the draft).

- [ ] **Step 1:** Extend the contract tests: `sip.call.conversational` is in the used vocabulary; operations `createVoiceCall`, `getVoiceCall`, `hangupVoiceCall`, `openaiChatCompletions`, `openaiTranscriptions`, `openaiSpeech` exist.
- [ ] **Step 2:** Run `uv run pytest -q tests/contract/test_broker_contract_files.py`. Expected: FAIL.
- [ ] **Step 3:** Add the paths and schemas; validate with `openapi_spec_validator`.
- [ ] **Step 4:** Tests pass. The route-parity test fails until Task 4/5 add the fake routes, so it is run in Task 5.
- [ ] **Step 5:** Commit "Broker draft: voice calls and an OpenAI-compatible model facade".

### Task 3: Speech server (`packages/speech_server`, `crewquarters-speech`)

**Files:**
- Create: `packages/speech_server/pyproject.toml` (deps: fastapi, uvicorn, numpy, soundfile? (no: use stdlib `wave`), python-multipart, `sherpa-onnx==1.13.8` as extra `sherpa`)
- Create: `packages/speech_server/src/crewquarters_speech/{__init__,app,engines,fake,sherpa,audio,download,cli,settings}.py`
- Create: `packages/speech_server/Dockerfile`
- Test: `packages/speech_server/tests/test_api.py`, `test_audio.py`, `test_sherpa_models.py` (`models` marker)

**Interfaces:**
- `class SpeechEngine(Protocol)`: `transcribe(pcm: np.ndarray[float32], sample_rate: int) -> str`; `synthesize(text: str, voice: str, speed: float) -> Iterator[bytes]` (s16le mono at 24000 Hz, one chunk per sentence).
- `create_app(settings: SpeechSettings, engine: SpeechEngine | None = None) -> FastAPI`.
- HTTP:
  - `POST /v1/audio/transcriptions` (multipart `file`, `model`, optional `language`, `response_format` in {json, text}) → `{"text": ...}`;
  - `POST /v1/audio/speech` JSON `{model, input, voice, response_format in {pcm, wav}, speed}` → streamed audio (`audio/pcm; rate=24000` or `audio/wav`);
  - `GET /v1/models`; `GET /health/ready`.
- `SherpaSpeechEngine.synthesize` splits text into clauses (commas and sentence ends, at least 3 words) and yields one PCM chunk per clause.
- `FakeSpeechEngine`: `transcribe` returns queued texts (`queue_transcripts(list[str])`) or `""`; `synthesize` returns 60 ms of a 440 Hz tone per word (deterministic, audible for VAD).
- `audio.decode(upload: bytes) -> (np.ndarray, int)` accepts WAV (any rate, 16-bit), resamples to 16 kHz for sherpa.
- CLI `crewq-speech serve|download [--models-dir]`. `download` fetches the two sherpa archives by pinned URL and SHA-256, and extracts them into `models-dir`.

- [ ] **Step 1:** Write tests for the fake engine through HTTP: transcription returns the queued text; speech streams PCM whose length is proportional to the word count; `response_format=wav` returns a valid WAV header; an unknown model gives 404 `MODEL_NOT_FOUND`; an empty input gives 422.
- [ ] **Step 2:** Run them. Expected: FAIL (package missing).
- [ ] **Step 3:** Implement the package and the sherpa engine (`OfflineRecognizer.from_transducer(..., model_type="nemo_transducer")`, `OfflineTts(OfflineTtsConfig(model=OfflineTtsModelConfig(kokoro=OfflineTtsKokoroModelConfig(...))))`, voice name → speaker id map from `voices.bin` order).
- [ ] **Step 4:** Tests pass; `-m models` test transcribes Kokoro's own "hello, this is a test" back to text containing "hello" and "test", and reports latency.
- [ ] **Step 5:** Dockerfile (python:3.12-slim pinned; models volume `/models`; non-root). Commit "Speech server: OpenAI-compatible STT and TTS on sherpa-onnx".

### Task 4: Fake platform: OpenAI-compatible facade

**Files:**
- Create: `packages/fake_platform/src/crewquarters_fake/broker/openai_compat.py`
- Create: `packages/fake_platform/src/crewquarters_fake/speech.py` (`SpeechClient`: HTTP to `CREWQ_FAKE_SPEECH_URL`, or an in-process `FakeSpeechEngine`)
- Modify: `broker/__init__.py`, `gateway.py` (tool calls from rules: `respond: {toolCall: {name, arguments}}`; pass `tools` to OpenAI-compatible servers), `llm_rules.py`, `settings.py`, `store.py` (speech log), `admin.py` (state `speech`, `POST /fake/v1/speech/transcripts`)
- Test: `packages/fake_platform/tests/test_openai_compat.py`

**Interfaces:**
- `/internal/v1/sdk/openai/v1/chat/completions`: JSON or SSE (`data: {"choices":[{"delta":{...}}]}` … `data: [DONE]`). It requires `llm.profile:<model>` and the model must be a bound variant; audit `llm.call`.
- `/audio/transcriptions` and `/audio/speech`: model must be `local.stt.*` / `local.tts.*` and granted; audit `speech.call` with `{profile, operation, seconds|characters}`, never content.
- Fake speech: `FakeSpeechEngine` from `crewquarters_speech.fake` (the fake platform depends on `crewquarters-speech` without the `sherpa` extra).

- [ ] **Step 1:** Tests:
  - streaming chat with the openai SDK (`openai.AsyncOpenAI(base_url=..., api_key=token)`) yields text deltas;
  - a rule with `toolCall` yields a `tool_calls` delta;
  - an ungranted model → 403 plus a `capability.denied` audit;
  - transcription returns the queued transcript;
  - speech streams PCM;
  - an audit record exists without content.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass.
- [ ] **Step 5:** Commit "Fake platform: OpenAI-compatible model facade with fake speech".

### Task 5: Fake platform: voice calls and the simulated callee

**Files:**
- Create: `packages/fake_platform/src/crewquarters_fake/broker/voice.py` (routes)
- Create: `packages/fake_platform/src/crewquarters_fake/voice/{__init__,backend,offline,livekit,callee}.py`
- Modify: `scenario.py` (`voice.callees: {"+E164": {outcome: answer|busy|no-answer|voicemail|fail, script: [...], ringSeconds}}`), `store.py` (voice calls), `settings.py` (`livekit_url`, `livekit_agent_url`, `livekit_api_url`, `livekit_api_key`, `livekit_api_secret`, `sip_trunk_id`), `admin.py` (state `voice`), `app.py` (backend selection)
- Test: `packages/fake_platform/tests/test_voice_routes.py`, `tests/voice/test_livekit_backend.py` (`voice`)

**Interfaces:**
- `class VoiceBackend(Protocol)`: `async def dial(call: VoiceCallRecord, scenario: CalleeScenario | None) -> None`, `async def hangup(call) -> None`, `def room_for(call) -> VoiceRoom`.
- `OfflineVoiceBackend` drives state from the scenario on a clock (ringing → answered/busy/no-answer), for route tests.
- `LiveKitVoiceBackend`: `api.LiveKitAPI(url, key, secret)`, `room.create_room`, `AccessToken(...).with_identity("agent").with_grants(VideoGrants(room_join=True, room=name, can_publish=True, can_subscribe=True))`, then either `sip.create_sip_participant(CreateSIPParticipantRequest(sip_trunk_id=..., sip_call_to=..., room_name=..., participant_identity="callee"))` or `SimulatedCallee.start()`.
- `SimulatedCallee`: joins as `callee`, publishes a 24 kHz track, waits `ringSeconds`, then per outcome:
  - `answer`: for each line, wait until the agent has spoken and gone quiet for 0.8 s, register the line with the speech service, then speak it (TTS);
  - `voicemail`: one 10 s monologue;
  - `busy`/`no-answer`/`fail`: never joins, sets the state.

  It leaves after the script and a closing silence.

- [ ] **Step 1:** Route tests with `OfflineVoiceBackend`:
  - dial requires `sip.call.conversational` (403 + audit otherwise);
  - dial is idempotent by key (same id, one call recorded);
  - `toMasked` never contains the full number;
  - states progress (`ringing` → `answered`);
  - `busy` scenario;
  - hangup → `completed` with a duration;
  - `get` of another run's call → 404.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass. Run the route-parity and traffic-conformance contract tests; update the traffic run list later (Task 9).
- [ ] **Step 5:** Commit "Fake platform: voice calls through LiveKit with a simulated callee".

### Task 6: SDK: `ctx.voice` and `ctx.model_endpoint()`

**Files:**
- Create: `packages/python_sdk/src/crewquarters/voice.py`
- Modify: `context.py` (`voice`, `model_endpoint()`), `__init__.py`, `_models.py` if needed
- Test: `packages/python_sdk/tests/test_voice.py`, `fakebroker.py` (voice routes)

**Interfaces:**
```python
class VoiceRoom(WireModel): url: str; name: str; token: str; identity: str
class VoiceCall(WireModel):
    id: str; idempotency_key: str; to_masked: str
    state: Literal["dialing","ringing","answered","completed","busy","no-answer","failed","canceled"]
    room: VoiceRoom | None; callee_identity: str | None
    answered_at: datetime | None; ended_at: datetime | None
    duration_seconds: int | None; error_code: str | None
class VoiceClient:
    async def dial(self, to: str, *, idempotency_key: str, ring_timeout_seconds: int = 30,
                   max_duration_seconds: int = 300) -> VoiceCall
    async def get(self, call_id: str) -> VoiceCall
    async def hangup(self, call_id: str) -> VoiceCall
@dataclass(frozen=True)
class ModelEndpoint: base_url: str; api_key: str   # ctx.model_endpoint()
```
- `dial` validates E.164 before any request, like telephony.

- [ ] **Step 1:** Tests:
  - dial posts the body and parses `VoiceCall`;
  - a non-E.164 number raises `InvalidInput` with no request;
  - dial is retried on 503 only because it carries the key;
  - hangup;
  - `model_endpoint()` is `<broker>/internal/v1/sdk/openai/v1` with the run token.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit "SDK: voice calls and an OpenAI-compatible model endpoint".

### Task 7: Agent core: ported pure modules, prompts, config, approval, outcome, results

**Files (package `agents/voice_caller`, module `voice_caller`):**
- Create:
  - `voicemail.py`, `texture.py`, `safety.py`, `metrics.py`, `transcript.py` (ported from truestar-voice `4c34cce`, header comment naming the source);
  - `prompts.py`, `config.py`, `approval.py`, `outcome.py`, `results.py`
- Test: `tests/test_voicemail.py`, `test_texture.py`, `test_safety.py`, `test_metrics.py` (ported), `test_prompts.py`, `test_config.py`, `test_approval.py`, `test_outcome.py`, `test_results.py`

**Interfaces:**
- `VoiceCallerConfig` fields:
  - `spreadsheetId`, `inputRange` (`Contacts!A2:D`), `resultRange` (`Results!A:J`);
  - `agentName` (default "Sam"), `organization`, `purpose`, `talkingPoints: list[str]`, `questions: list[str]`;
  - `disclosure` (default "This is an automated call from an AI assistant");
  - `maxCalls` (≤ 25), `maxCallSeconds` (60–900, default 240), `ringTimeoutSeconds` (default 30), `voice` (default `af_sarah`);
  - `modelProfile`, `sttProfile`, `ttsProfile` (widgets `modelProfile`).
- `build_system_prompt(config, contact_first_name) -> str` contains the disclosure and honesty rules, never the words "real person", and keeps the brevity rules.
- `build_greeting(config, first_name, rng) -> str` starts with a greeting and the disclosure within the first two sentences.
- `CallOutcome(BaseModel)`: `disposition: Literal[...]`, `interest: Literal["high","medium","low","none","unknown"]`, `callback_requested: bool`, `follow_up: str`, `notes: str` (≤ 300 chars).
- `classify_transcript(...)` is the LLM prompt builder plus `outcome_for_unanswered(state) -> CallOutcome`.
- `results.HEADER = ["source_row","name","phone_masked","call_id","disposition","interest","callback","notes","duration_seconds","completed_at"]` and `row_values(...)`.

- [ ] **Step 1:** Port the truestar tests for voicemail, texture, safety, and metrics, adapting imports. Write new tests:
  - prompt contains the disclosure; prompt has no AI denial (`"real person" not in prompt`, `"not an AI" not in prompt`);
  - the greeting's first two sentences contain the disclosure;
  - the config rejects `maxCalls` > 25;
  - the approval key changes when the brief changes;
  - an outcome with "don't call me again" in the transcript and the model saying `dnc` stays `dnc`;
  - `outcome_for_unanswered("busy").disposition == "busy"`;
  - result row values mask the phone number.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit "Voice agent: ported conversation modules, honest prompts, config, approval, outcomes".

### Task 8: Agent runtime: turn detection, call session, run orchestration

**Files:**
- Create: `turn_detection.py`, `session.py`, `agent.py`, `__main__.py`, `manifest.yaml`, `Dockerfile`, `pyproject.toml`, `scenarios/default/{scenario.yaml,config.yaml}`
- Test: `tests/test_turn_detection.py`, `test_conversation.py` (text mode), `test_agent_run.py` (orchestration with a stub call runner)

**Interfaces:**
- `class InProcessInference(InferenceExecutor)`: `async def do_inference(self, method: str, data: bytes) -> bytes | None`. It runs `_EUORunnerMultilingual().run(data)` in a worker thread after `initialize()`.
- `def build_turn_detector() -> MultilingualModel | None`: returns `MultilingualModel(inference_executor=InProcessInference())`, or `None` if the weights are missing (falls back to VAD endpointing, logged).
- `class VoiceCallAgent(Agent)` with `end_call` `@function_tool`, and `tts_node` stripping brackets and applying the texture openers.
- `async def run_call(ctx, call: VoiceCall, contact: ContactRow, config) -> CallReport`, where `CallReport{answered, transcript: list[Turn], ended_by, duration_seconds, error_code}`. It:
  - connects `rtc.Room` with the call's token;
  - waits for `callee` within the ring timeout while polling `ctx.voice.get`;
  - builds the `AgentSession`;
  - runs the pickup wait, probe, and greeting;
  - runs the voicemail poll;
  - enforces `maxCallSeconds`;
  - hangs up through `ctx.voice.hangup`.
- `agent.py`: `@agent.run` orchestration:
  - read the sheet, classify, approve;
  - per row: `once(dial)`, `run_call`, classify the outcome, write the result row, report progress;
  - `in_doubt` → `failed`/`OUTCOME_UNKNOWN`;
  - the result is `{operatorDecision, summary{...}, rows[...]}`.

  `CallRunner` is injectable for tests.

- [ ] **Step 1:** Tests:
  - the in-process executor returns an EOU probability for a short chat (skipped when the weights are absent in CI; the weights are fetched in the voice CI job);
  - conversation (text mode, openai.LLM against an in-process fake platform with rules):
    - greeting flow;
    - "not interested" → `end_call` tool called;
    - "are you a robot?" → reply contains "automated" or "AI assistant";
  - orchestration with a stub runner: `test_unanswered_call_is_recorded_and_the_run_continues`, `test_model_failure_fails_only_that_call`, `test_retried_run_never_redials_a_row`, `test_dnc_row_is_marked_and_skipped_next_run`, and operator cancel places zero calls.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5:** Manifest (spec §7) validates with `crewctl validate --allow-unbuilt`. Dockerfile bakes the weights (`silero.VAD.load()`; `_EUORunnerMultilingual._download_files()`). Commit "Voice agent: in-process turn detection, LiveKit call session, run orchestration".

### Task 9: Local voice stack, E2E, and CI

**Files:**
- Create: `infra/livekit/livekit.dev.yaml` (keys, `rtc.tcp_port: 7881`, `rtc.udp_port: 7882`, `node_ip: 127.0.0.1`), `infra/livekit/sip.yaml` (GB10 template)
- Modify: `infra/compose/compose.yaml`:
  - profile `voice`: `livekit`, `speech` (the speech server with the `speech-models` volume);
  - profile `voice-sip`: `redis`, `livekit-sip`;
  - `fake-platform` env for LiveKit and speech.
- Modify: `Makefile` (`voice-up`, `voice-down`, `speech-models`, `voice-e2e`, `voice-demo`), `.github/workflows/ci.yml` (job `voice-e2e`: LiveKit service container + fake speech), `pyproject.toml` (markers `voice`, `models`; testpaths `tests/voice`; workspace members), `conftest.py` (DB-free roots)
- Create: `tests/voice/conftest.py`, `tests/voice/test_voice_e2e.py`
- Modify: `tests/contract/test_fake_traffic_conformance.py` (the voice agent in the traffic run, via a stub-free text path when LiveKit is absent)

**Interfaces:**
- `test_voice_e2e.py`, with marker `voice`, needs a LiveKit server at `CREWQ_VOICE_LIVEKIT_URL` (default `ws://127.0.0.1:7880`):
  - scenario with callees `answer` (4-line script), `voicemail`, `busy`;
  - asserts dispositions `completed`/`declined`, `voicemail`, `busy`;
  - the agent transcript contains the disclosure;
  - the callee's lines appear in the agent's transcript in order;
  - no full numbers in logs or results.
- `-m "voice and models"` runs the same against the real speech server.

- [ ] **Step 1:** Write the E2E test (it skips cleanly when LiveKit is unreachable).
- [ ] **Step 2:** `make voice-up && uv run pytest -m voice tests/voice`. Expected: FAIL until wired.
- [ ] **Step 3:** Wire compose, the Makefile, and the fake platform settings.
- [ ] **Step 4:** Pass locally with fake speech; then run with real models.
- [ ] **Step 5:** Commit "Local voice stack: LiveKit and speech in Compose, voice E2E, CI job".

### Task 10: Docs, decision log, and final verification

**Files:**
- Create: `docs/voice/README.md` (architecture, laptop quickstart, GB10 deployment: LiveKit `node_ip`, SIP ports, trunk setup, vLLM model choice, speech on CPU with the GPU follow-up)
- Modify: `README.md` (voice section), `docs/decisions/0001-person5-contract-drafts.md` (D22–D25), `docs/release/checklist.md`, `docs/sdk/reference.md` (`ctx.voice`, `ctx.model_endpoint()`)

- [ ] **Step 1:** Write the docs.
- [ ] **Step 2:** Full verification:
  - `make lint`;
  - `make coverage` (PostgreSQL);
  - `make contracts-check`;
  - `make fake-up && make e2e`;
  - `make voice-up && make voice-e2e`;
  - the models run;
  - gitleaks.
- [ ] **Step 3:** Final whole-branch review; fix Critical/Important findings with TDD.
- [ ] **Step 4:** Commit "Docs: voice call center agent, local voice stack, contract decisions".
