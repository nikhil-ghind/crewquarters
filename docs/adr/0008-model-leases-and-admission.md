# ADR 0008: Model leases, admission control, and on-demand residency

- Status: Accepted
- Date: 2026-09-25
- Owner: Akshay Sunil Navani (Person 2)

## Context

On the GB10 appliance, model weights, KV cache, the OS, the database and agents all share 128 GB of coherent unified memory (PLAN.md section 3). A downloaded model must stay on disk, but it should occupy memory only while something is using it (PLAN.md section 1: "if the model is not in use it should be unloaded"). vLLM's sleep endpoints require development mode and must not be exposed (PLAN.md section 8.2).

## Decision

**Two independent states.** `model_installations.state` tracks files on disk (`NOT_INSTALLED → DOWNLOADING → INSTALLED`, plus `DOWNLOAD_ERROR` and `DELETING`). `model_instances.state` tracks memory (`NOT_LOADED → LOADING → READY → DRAINING → NOT_LOADED`, plus `LOAD_ERROR` and `RUNTIME_ERROR`). The API and UI never merge them into one "active" flag.

**Leases.** Every user of a local model holds a row in `model_leases`, unique per `(model, holder)` among active leases:
- a run holds one for `run_lease_ttl` (300 s), renewed on every call, and released when the run is no longer active;
- a chat session holds one while it is enabled;
- a manual load takes one momentarily.

A lease request for a model that is not resident enqueues exactly one `model.load` job (dedupe key `model:{id}:load`). Concurrent callers wait on the same instance.

**Admission control** runs when a load is requested, while the instance row is locked:
- `reserved + startup_peak + margin <= max_serving` (defaults: 96 GiB serving cap, 8 GiB margin);
- host `MemAvailable - system_reserve >= startup_peak + margin` (default reserve: 24 GiB), from the daemon's live telemetry;
- one generative model at a time by default.

A refusal returns `409 MODEL_CAPACITY_EXCEEDED` with the numbers and the loaded models, so the UI can offer to unload an idle one. The more conservative of the catalog estimate and the telemetry always wins.

**Release** means stopping the container, never sleeping it. The runtime daemon stops and removes the serving container. The gateway marks `NOT_LOADED` only after the container is confirmed gone, and records memory samples from before and after.

**Unloading:**
- The reaper sets `idle_since` when the last lease goes away, and starts an idle drain after `idle_unload_seconds` (600 s).
- A new lease that arrives during an idle drain cancels it.
- A manual unload refuses while leases are held, unless `force` is set. It blocks new leases, waits up to `manual_drain_seconds` for in-flight requests, then stops the container.
- The reaper never unloads a model that has an active lease.

**Crash reconciliation.** A `READY` model whose container is no longer running becomes `RUNTIME_ERROR`, and its reservation is released. The next lease reloads it.

**Runs see the load.** While a run waits for a cold model, the gateway moves it to `LOADING_MODEL` through the control API. It moves back to `RUNNING` when the model is ready. The model signal can never pull a run out of `WAITING_INPUT`.

**Trusted launch.** Launch profiles, meaning the image digest, arguments, GPU flag, IPC mode and user, come only from the root-owned catalog directory. Callers name a model ID; they never pass flags (PLAN.md section 5.2).

## Consequences

- Cold starts are visible and bounded, through the `stage` field, `LOADING_MODEL`, and `startupTimeoutSeconds`.
- Serving memory is released deterministically, at the cost of a reload after the idle timer.
- The default policies are conservative estimates until the GB10 benchmark (`docs/benchmarks/gb10.md`) replaces them with measured peaks.
- Running several small models concurrently remains an explicit, measured setting (`one_generative_model=false`).
