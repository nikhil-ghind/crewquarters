# Laptop performance report (PLAN.md section 22)

Status: **measured on a laptop, 2026-09-25**, commit `7397c18` plus the harness in `infra/scripts/perf/`. Raw numbers: [`laptop-results.json`](laptop-results.json). GB10-only targets are marked **pending hardware**. See `gb10.md` for how to run the same harness on the device.

## Machine

| Field | Value |
| --- | --- |
| CPU | Intel Core Ultra 7 255H, 16 cores (x86_64) |
| Memory | 30.2 GiB (about 17 GiB available during the run; other stacks were running) |
| OS / kernel | Ubuntu, Linux 7.0.0-31-generic |
| Docker | 29.4.3, Compose v2 |
| GPU | none used (mock model server) |
| Stack | `infra/compose/compose.yaml` + `compose.runtime.yaml` + `infra/scripts/perf/compose.perf.yaml`, project `cqperf`, proxy on `127.0.0.1:18094` |
| Embeddings | `CQ_EMBEDDING_MODE=local`: pinned `jina-embeddings-v2-small-en` ONNX on the CPU (profile `local.embedding.jina-v2-small-en`) |
| Runtime | Containerized runtime daemon (dev only), real hardened agent containers, mock model-server containers |

## Method

`make perf` builds `crewquarters/platform:perf` and `crewquarters/proxy:perf`, then runs `infra/scripts/perf/perf.py all`. The harness:

1. Starts its own Compose project from empty volumes (`cqperf`, its own ports, image tags, data directory, and runtime-daemon networks `cqperf-agents`/`cqperf-models`), plus a local registry on `127.0.0.1:15021`. It never touches another stack.
2. Builds the `contract-probe` agent for this machine, pushes it to that registry, and pins its digest (`crewctl build --push`).
3. Measures each section through the edge proxy as the browser would (cookie session, CSRF header, `Origin`):
   - **setup**: `compose up --wait` from empty volumes, then owner bootstrap, model install, agent import and install, and a first real agent run that succeeds;
   - **api**: 13 endpoints (9 reads, 4 writes) at concurrency 1, 16 and 64, with 200 to 640 requests per endpoint per level;
   - **dispatch**: 10 sequential runs of the real agent container (image already present), then a burst of 12 concurrent runs. SSE latency is `receive time − event createdAt` for every live event on `GET /runs/{id}/events`;
   - **models**: mock `local.general.small`. It measures 3 manual cold starts until READY, manual unload, 20 admission refusals of a second generative model, 20 warm chat replies (first streamed token), and idle unload after the chat lease is released, with `CQ_GATEWAY_IDLE_UNLOAD_SECONDS=60`;
   - **ingest**: 12 generated Markdown handbooks (500 KB) uploaded through the API, timed until every document is `READY`;
   - **query**: seeds 10k and then 100k chunks into one knowledge base with the knowledge service's own table models (`Document`, `DocumentChunk`; 512-dim unit vectors, about 420 words per chunk). Real text queries then go through `POST /api/v1/knowledge-bases/{id}/query`, which embeds the query with the ONNX model and runs the service's exact-search SQL. The same queries are run again after an **ad hoc** HNSW index (`vector_cosine_ops`, pgvector defaults), which is then dropped. No migration is added.
4. Tears down containers, volumes, networks and data.

Percentiles are nearest-rank. The run was on a busy development laptop (other Compose stacks were up), so treat the numbers as an upper bound for an idle appliance.

## Results against PLAN.md section 22

| PLAN §22 metric | Target | Measured on this laptop | Result |
| --- | --- | --- | --- |
| API non-inference p95 on an idle appliance | < 300 ms | Worst endpoint p95 **38.5 ms** at c=1 (`GET /attention`). At c=16 the worst p95 is 221 ms. At c=64 the worst is 1,123 ms. | **PASS** (idle, c=1) |
| Online schedule dispatch error | ≤ 5 s | Not measured by the harness. Covered by the deterministic-clock scheduler tests. The dispatch path (below) takes about 0.2 s from run creation to `PREPARING`. | n/a here |
| UI event visibility after server event | < 2 s | SSE p50 99 ms, p95 **510 ms**, max 518 ms (n = 690 live events) | **PASS** |
| Agent container start after image present | < 10 s | Run created → container started p50 327 ms, p95 **522 ms**. Created → SDK handshake (`RUNNING`) p50 1.9 s, p95 2.6 s. With a burst of 12 concurrent runs, created → `RUNNING` p95 is 7.9 s. | **PASS** |
| Small-model cold start | Measured and documented; UI shows progress; hard timeout 10 min | Mock server: manual load → `READY` p50 2.24 s. That is the container start plus the catalog's 1 s simulated delay plus the gateway health check. | n/a on laptop: **pending hardware** (GB10, real vLLM) |
| Small-model warm first-token latency | Measured baseline; regression threshold after GB10 test | Mock model through proxy → control API → gateway → model container: first token p50 47 ms, p95 64 ms. This is platform overhead only. | n/a on laptop: **pending hardware** |
| Knowledge query p95 at 100k chunks, excluding generation | < 1 s on target hardware | **Exact search: 426 ms** p95 through the API (SQL alone p95 147 ms). HNSW (ad hoc): 49 ms p95, recall@8 0.994. | **PASS without the index** on this laptop. The GB10 confirmation is **pending hardware**. |
| Idle model unload | Within 60 s after the configured grace | Unloaded **2.3 s** after a 60 s grace | **PASS** |
| Job recovery after worker death | Within lease expiry (30 s) | Not measured here. Covered by the queue tests that kill a worker between claim and completion. | n/a here |
| Duplicate external side effects in the injected retry suite | Zero | Not a performance measurement. Covered by the broker, telephony and idempotency suites. | n/a here |
| Fresh laptop setup after prerequisites | < 15 min excluding downloads | 27.4 s of machine time: `compose up` 24.7 s from empty volumes, plus API bootstrap → first successful agent run 2.7 s. Image builds are not included (they were cached). | **PASS** (machine part). An operator walkthrough is not measured. |
| Fresh GB10 core setup after prerequisites | < 30 min excluding model download | — | **pending hardware** |

## Detail

### Control API latency through the proxy (ms)

| Endpoint | c=1 p50 / p95 / p99 | c=16 p50 / p95 / p99 | c=64 p50 / p95 / p99 |
| --- | --- | --- | --- |
| GET /me | 5.3 / 8.9 / 12.6 | 32 / 106 / 142 | 137 / 193 / 235 |
| GET /runs?limit=20 | 6.7 / 9.6 / 11.2 | 54 / 108 / 139 | 184 / 978 / 1621 |
| GET /runs/{id} | 7.4 / 10.5 / 13.2 | 51 / 81 / 92 | 213 / 646 / 1103 |
| GET /agent-installations | 18.3 / 29.9 / 33.4 | 80 / 211 / 229 | 335 / 1090 / 1352 |
| GET /catalog/agents | 7.5 / 11.0 / 12.4 | 63 / 113 / 141 | 201 / 957 / 1401 |
| GET /models | 17.5 / 29.2 / 37.3 | 88 / 165 / 204 | 285 / 740 / 1084 |
| GET /knowledge-bases | 12.2 / 18.6 / 22.4 | 61 / 131 / 191 | 196 / 882 / 1531 |
| GET /attention | 26.5 / 38.5 / 44.2 | 110 / 216 / 258 | 378 / 974 / 1276 |
| GET /system/status | 26.9 / 33.8 / 38.0 | 76 / 140 / 172 | 235 / 1123 / 1760 |
| PATCH /settings | 12.2 / 20.0 / 26.2 | 54 / 220 / 312 | 310 / 994 / 1640 |
| POST /schedules/preview | 6.6 / 9.3 / 10.3 | 49 / 67 / 79 | 195 / 747 / 1506 |
| POST /knowledge-bases | 17.3 / 25.4 / 32.7 | 61 / 126 / 148 | 281 / 1000 / 1457 |
| DELETE /knowledge-bases/{id} | 15.5 / 24.3 / 29.0 | 58 / 101 / 134 | 256 / 992 / 1429 |

There were no errors at any level. Throughput peaks at about 190–420 requests/s at c=64. The control API is a single uvicorn process, and the tail at c=64 is queueing in that process. The target is about an idle, single-owner appliance, so c=64 is headroom information, not a requirement.

### Run dispatch and events

| Step (image present, sequential) | p50 | p95 |
| --- | --- | --- |
| `POST /runs` response | 52 ms | 160 ms |
| Created → `PREPARING` (worker claimed the dispatch job) | 169 ms | 341 ms |
| Created → agent container started (Docker `StartedAt`) | 327 ms | 522 ms |
| Created → `RUNNING` (SDK handshake from inside the container) | 1.88 s | 2.65 s |
| Created → `SUCCEEDED` (handshake and events checks) | 2.05 s | 2.80 s |

About 1.4 s of the time to `RUNNING` is Python interpreter and SDK start-up inside the agent image. SSE latency (p95 510 ms) is dominated by the stream's 0.5 s database poll (`SSE_POLL_SECONDS`), not by delivery.

### Models (mock server, `CQ_GATEWAY_RUNTIME=daemon`)

| Measurement | Value |
| --- | --- |
| Install (bundled mock files) | 0.05 s |
| Manual load → `READY` | p50 2.24 s, max 2.32 s. The server reports 2.05–2.22 s from load start to ready. |
| Manual unload → `NOT_LOADED` (container stopped and removed) | p50 0.96 s |
| Admission: loading `local.general.quality` while `small` is resident | Refused with `409 MODEL_CAPACITY_EXCEEDED` (one generative model by default), p50 15.5 ms, p95 22.3 ms |
| Warm chat, first streamed token / full reply | p50 47 / 55 ms, p95 64 / 73 ms |
| Idle unload | Chat disabled → `NOT_LOADED` in 62.3 s with a 60 s grace, so 2.3 s late. The reaper runs every 2 s. |

### Knowledge ingestion (real pipeline, local ONNX embeddings)

| Documents | Bytes | Chunks | Upload (12 requests) | Upload → all `READY` | Throughput |
| --- | --- | --- | --- | --- | --- |
| 12 Markdown | 500 KB | 144 | 0.29 s | 44.9 s | **3.2 chunks/s** (0.64 MiB/min) |

Embedding dominates. A direct check inside the knowledge container measured 0.54 s per ~800-token chunk (17.2 s for a batch of 32). At this rate, ingesting 100k chunks through the pipeline would take about 9 hours on this laptop. That is why the query benchmark seeds chunks directly. PLAN.md sets no ingestion target. This number is a baseline, and it should be re-measured on the GB10's Arm cores.

### Knowledge query: exact search vs. an ad hoc HNSW index

The chunks are all in one knowledge base. Queries are 20 natural-language questions × 5 rounds, embedded by the real model.

| Chunks | Table size | Exact: API p50 / p95 / p99 (c=1) | Exact: SQL only p95 | Exact: API p95 at c=4 | HNSW build | HNSW: API p50 / p95 (c=1) | HNSW: SQL p95 | HNSW recall@8 vs. exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10,000 | 87 MB | 51 / 83 / 92 ms | 26 ms | 232 ms | 1.3 s | 61 / 96 ms (the planner kept the exact plan) | 25 ms | 1.000 |
| 100,000 | 458 MB (719 MB with the index) | 303 / **426** / 443 ms | 147 ms (parallel seq scan, 2 workers) | 614 ms | 10.0 s | 31 / **49** ms | 7.4 ms | 0.994 |

The gap between the API and SQL-only numbers (about 25–30 ms at 10k chunks) is embedding the question on the CPU, plus the proxy and the two HTTP hops. At 10k chunks, PostgreSQL does not use the index for the filtered query; it chooses the exact plan.

**Does the target need the index?** Not on this laptop. Exact search at 100k chunks is 426 ms p95 at c=1, well under 1 s. It grows linearly, though: roughly 1.5 ms of SQL time per 1k chunks, with two parallel workers, and it approaches 1 s under concurrency (614 ms p95 at c=4). The GB10's Arm cores and shared memory bandwidth may be slower for this scan, so the decision stays open until the GB10 run. If an index is added later:

- use a migration;
- keep in mind that pgvector applies the `kb_id` filter after the HNSW candidate search, so a small knowledge base in a large table can return fewer than `topK` passages unless `hnsw.ef_search` is raised or iterative scans are enabled;
- note that the index adds 57% to the table size and 10 s of build time at 100k chunks.

Harness note: a parallel HNSW build needs more than Docker's default 64 MiB `/dev/shm`. `compose.perf.yaml` gives the perf stack's PostgreSQL 2 GiB. An appliance migration that builds the index would need the same, or `max_parallel_maintenance_workers = 0`.

## Gaps and caveats

- Model numbers come from the mock server. Real cold start, first token, prefill/decode throughput and memory are GB10-only and **pending hardware** (`gb10.md`).
- The laptop runs the runtime daemon in a container (development only). On the appliance it is a host systemd service. Container start times should be similar, but they have not been measured there.
- Schedule dispatch error, job recovery and duplicate side effects are covered by the automated test suites, not by this harness.
- The run shared the laptop with other Compose stacks, so the latencies are pessimistic for an idle appliance.
- The first harness pass hit two harness bugs: container `StartedAt` parsing, and the PostgreSQL shared memory size for the HNSW build. After fixing them, the dispatch and query sections were re-run on the same stack. `laptop-results.json` records this in `notes`.

## How to reproduce

```bash
make perf                                  # build images, run all sections, tear down
PERF_ARGS="--only api,dispatch" make perf  # a subset
PERF_ARGS="--keep" make perf               # leave the cqperf stack up for inspection
uv run python infra/scripts/perf/perf.py down          # tear it down afterwards
uv run python infra/scripts/perf/perf.py report tmp/perf/results.json   # the §22 table
```

Ports (`PERF_HTTP_PORT`, `PERF_POSTGRES_PORT`, `PERF_REGISTRY_PORT`), the project name (`PERF_PROJECT`), the image tag (`PERF_TAG`), the data directory (`PERF_DATA_DIR`) and the embedding mode (`PERF_EMBEDDING_MODE=local|fake`) are overridable. The defaults are 18094, 15446, 15021, `cqperf`, `perf`, `/tmp/cqperf-data` and `local`.
