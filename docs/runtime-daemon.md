# Runtime daemon

Owner: Akshay Sunil Navani (Person 2). Package `crewquarters_runtime` (`services/runtime_daemon`). It uses only the Python standard library and runs on Python 3.10 and later, so the arm64 `.deb` ships no native wheels. It runs as `crewquarters-runtime.service` behind `crewquarters-runtime.socket`.

It is the only component with Docker access. It listens on a Unix socket (never TCP), requires the internal service token, and builds every container configuration itself from validated platform records.

## API (PLAN.md section 5.2)

| Route | Behavior |
| --- | --- |
| `GET /internal/v1/host/capacity` | Architecture, memory, disk, Docker/NVIDIA runtime, GPU (`nvidia-smi`), network isolation mode |
| `POST /internal/v1/images/pull` | Pulls by digest only; refuses mutable references |
| `POST /internal/v1/runs` | Starts a hardened agent container. Idempotent on `(run_id, attempt)`, returning the same `runtimeRef` |
| `GET /internal/v1/runs/{ref}`, `GET /internal/v1/runs/{ref}/logs?tail=N` | Status and exit code; bounded logs |
| `POST /internal/v1/runs/{ref}/cancel` | SIGTERM, a grace period, then SIGKILL; removes the container and its config directory |
| `GET /internal/v1/models/{id}` | File state and container state/endpoint |
| `POST /internal/v1/models/{id}/install`, `/install/cancel`, `DELETE /internal/v1/models/{id}/files` | Download, cancel, delete |
| `POST /internal/v1/models/{id}/start`, `/stop` | Starts the allowlisted server; stops it and verifies the release |

## Agent container hardening

Each run gets:
- a non-root user (65532), a read-only root filesystem, and a 64 MiB `noexec` tmpfs at `/tmp`;
- all capabilities dropped, `no-new-privileges`, not privileged, private IPC, and an init process;
- CPU, memory (no swap) and PID limits;
- the internal agent network in isolated gateway mode, with no route to the host, the LAN, the internet or other networks. The daemon creates this network (`cq-agents`) at startup. The capability broker is the only other member: Compose attaches it as an external network with the alias `capability-broker`, so `PLATFORM_BROKER_URL=http://capability-broker:8000` resolves and nothing else does. The model gateway joins `cq-models` the same way;
- only `PLATFORM_*` environment variables;
- one read-only bind mount: its non-secret config at `/run/crewquarters/config.json`.

Any field the daemon does not recognize is ignored; it never reaches Docker. `services/runtime_daemon/tests/test_daemon.py` proves each property against a real engine: it checks that a container cannot reach the internet, DNS, the database, the host's gateway, LAN or DNS addresses, or the Docker socket.

## Model files

- **Sources:** `huggingface` (pinned 40-character commit, `allowPatterns`) or `bundled` (files shipped with the catalog, pinned by SHA-256).
- **Download process:** check free disk against a reserve (`CQ_RUNTIME_DISK_RESERVE_BYTES`, default 20 GiB), download into staging, resume with `Range`, verify size and SHA-256, write `crewquarters-manifest.json`, then atomically rename into `models/<id>/<revision>`. A restart mid-download becomes `DOWNLOAD_ERROR/INTERRUPTED`, which can be resumed.
- **Model containers** run the profile's pinned image with the model directory mounted read-only. They get GPU device requests and `ipc=host` only when the trusted profile says so, and an init process so unload is prompt.

## Configuration

| Variable | Default |
| --- | --- |
| `CQ_RUNTIME_SOCKET` | `/run/crewquarters/runtime.sock` (ignored under systemd socket activation) |
| `CQ_RUNTIME_SOCKET_MODE` | `0660` |
| `CQ_RUNTIME_TOKEN_FILE` / `CQ_INTERNAL_SERVICE_TOKEN` | Service token (file preferred) |
| `CQ_RUNTIME_DATA_DIR` | `/var/lib/crewquarters` |
| `CQ_RUNTIME_MODEL_PROFILES` | `/usr/share/crewquarters/catalog/models` |
| `CQ_RUNTIME_AGENT_NETWORK` / `CQ_RUNTIME_MODEL_NETWORK` | `cq-agents` / `cq-models` |
| `CQ_RUNTIME_ISOLATE_MODEL_NETWORK` | `true` |
| `CQ_RUNTIME_DISK_RESERVE_BYTES` | `21474836480` |
| `CQ_RUNTIME_HF_ENDPOINT` | `https://huggingface.co` |
| `CQ_RUNTIME_DOCKER_SOCKET` | `/var/run/docker.sock` |
