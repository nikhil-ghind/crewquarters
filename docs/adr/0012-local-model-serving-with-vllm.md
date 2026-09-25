# ADR 0012: Local model serving with vLLM

- Status: Accepted
- Date: 2026-09-25
- Owner: Akshay Sunil Navani (Person 2)

## Context

Agents and chat need a local LLM on the GB10 appliance. The same code must also run on laptops and in CI, where there is no GPU. PLAN.md sections 1 and 8 fix these requirements:

- Serving uses an NVIDIA-tested GB10 container with an OpenAI-compatible API.
- Only a small catalog of tested profiles is offered. There is no free-form Hugging Face box.
- A model occupies memory only while something uses it (ADR 0008).
- Agents never choose Docker options, images or flags.

## Decision

**Engine.** Local generative models are served by vLLM from NVIDIA's NGC image, pinned by digest: `nvcr.io/nvidia/vllm@sha256:15f380ad...` (25.09-py3). The gateway talks to it through `POST /v1/chat/completions` (`LocalAdapter` in `services/model_gateway/src/crewquarters_gateway/adapters.py`). Structured output uses `response_format: {type: json_schema}`.

**Catalog.** Each profile is one JSON file in `catalog/models/<set>/`. The file name must equal `id` (`ModelProfile.load` in `services/runtime_daemon/src/crewquarters_runtime/specs.py`). A profile pins:

- `source.repo` and `source.revision` (a Hugging Face commit), plus `allowPatterns`;
- `launch.image` (it must match `IMMUTABLE_IMAGE`, `name@sha256:<64 hex>`), `launch.args`, `launch.env`, `gpu`, `ipcHost`, `healthPath` and `startupTimeoutSeconds`;
- `memory.weightBytes` and `memory.startupPeakBytes`, which admission uses (ADR 0008);
- `validation.status`.

Two profile sets exist:

- `catalog/models/dgx/`: `local.general.small` is `Qwen/Qwen2.5-7B-Instruct` in bf16 with `--gpu-memory-utilization 0.25`. `local.general.quality` is `Qwen/Qwen2.5-32B-Instruct-AWQ` in AWQ int4 with `0.45`. Both use `--max-model-len 8192` and `--enable-prefix-caching`, with `HF_HUB_OFFLINE=1` and `VLLM_NO_USAGE_STATS=1`. Both are non-gated Apache-2.0 models.
- `catalog/models/dev/`: the same two ids, served by `catalog/models/dev/files/mock_openai_server.py`. This is a standard-library, deterministic, OpenAI-compatible mock. It runs in a digest-pinned `python` image as uid 65532, with no GPU and a 1-2 s simulated startup.

The gateway syncs a set into `model_catalog` at startup (`crewquarters_gateway/catalog.py`, `CQ_GATEWAY_CATALOG_DIR`, default `catalog/models/dev`). The daemon reads the same set from `CQ_RUNTIME_MODEL_PROFILES` (default `/usr/share/crewquarters/catalog/models`). The `.deb` installs the `dgx` set there (`infra/debian/build-deb.sh`).

**Family and variant.** `crewquarters_shared/manifest.py` implements PLAN.md section 8.1:

- `DEFAULT_VARIANTS` maps `local.general` to `local.general.small`.
- `resolve_model_bindings()` binds a family request to the default variant or to the owner's choice within that family. An exact variant cannot be changed (`INVALID_MODEL_BINDING`).
- The capability token carries the resolved variant (`llm.profile:<variant>`).

**Launch.** The runtime daemon builds the serving container only from the root-owned catalog (`model_container_config` in `specs.py`, `start_model` in `engine.py`).

- Only `{model_path}`, `{served_model_name}` and `{port}` are substituted into `launch.args`.
- Model files are bind-mounted read-only at `/models/<id>`.
- The container runs with `CapDrop: ALL`, `no-new-privileges` and `Init`, and no restart policy.
- It gets NVIDIA `DeviceRequests` only when `gpu` is true, and `IpcMode: host` only when `ipcHost` is true.
- It is attached to the internal `cq-models` network (`CQ_RUNTIME_MODEL_NETWORK`).
- A model not in the catalog is `NOT_FOUND` ("not in the allowlisted catalog"). The API accepts no free-form flags, mounts or devices.

**Downloads.** Files are staged in `models/.staging/<id>-<revision>`. The daemon checks disk space first (`CQ_RUNTIME_DISK_RESERVE_BYTES`, default 20 GiB), resumes with HTTP Range, and verifies size and SHA-256 where the source publishes one. It then renames the directory atomically to `models/<id>/<revision>` (`crewquarters_runtime/downloads.py`).

**Readiness.** The gateway marks a model `READY` only after `GET {healthPath}` (`/v1/models`) lists the profile id as a served model (`_healthy` in `manager.py`). It waits up to `CQ_GATEWAY_WAIT_READY_SECONDS` (900 s, the same as the dgx `startupTimeoutSeconds`).

**Residency.** Unload stops and removes the container, and never uses vLLM sleep mode (PLAN.md sections 8.2 and 25). Leases, admission control and idle unload are defined in ADR 0008 and are not repeated here.

**Test modes.** `CQ_GATEWAY_RUNTIME=inprocess` replaces the daemon with an in-process mock (`InProcessMockAdapter`). The laptop Compose stack uses this mode. `infra/compose/compose.runtime.yaml` runs the real daemon with the `dev` mock containers.

## Alternatives considered

- **Ollama or llama.cpp.** Good on laptops, but they are not the NVIDIA-tested GB10 path, and GGUF quantization and a separate model store would sit outside the pinned-revision flow (PLAN.md section 1).
- **TensorRT-LLM.** Listed as deferred in PLAN.md section 1. It adds a per-model build step to the pinned install flow.
- **SGLang.** Not evaluated for v1. It also offers an OpenAI-compatible API, but the plan pins one engine to NVIDIA's DGX Spark vLLM playbook (PLAN.md section 27), and supporting two engines would double the launch profiles to validate on the GB10.
- **vLLM sleep/wake instead of stopping the container.** Its endpoints require development mode and must not be exposed, and the memory benefit on unified memory is unproven (PLAN.md section 25). Deferred until it can be benchmarked safely.
- **Always-resident model.** It conflicts with the 128 GB shared memory budget (ADR 0008).

## Consequences

- **Cold starts.** Every first request after an idle unload pays the full vLLM startup, which can take up to 15 minutes. The UI shows progress, and the smaller model is the default.
- **Unvalidated numbers.** Both dgx profiles are still `validation.status: candidate` with `memory.validated: false`. Their memory figures are estimates until `infra/scripts/benchmark_model.py` runs on a GB10 and `docs/benchmarks/gb10.md` is filled in (its status is "not yet run"). *PLAN.md section 8.1 requires measured values before the demo.*
- **No embedding profile.** There is no `local.embedding.small` file in `catalog/models/`, although `KNOWN_VARIANTS` and `DEFAULT_VARIANTS` list it. Embeddings run on the CPU inside the knowledge service (ADR 0013), not through the gateway. *Divergence from PLAN.md section 8.1, which lists it as a catalog profile.*
- **Adding a model** means adding a reviewed JSON profile and rebuilding the package. Owners cannot add models at runtime.
- **Laptop tests.** Laptop and CI runs exercise the full lease, load, route and unload path against mocks, but not vLLM itself. The GB10 rehearsal is the only real test.
