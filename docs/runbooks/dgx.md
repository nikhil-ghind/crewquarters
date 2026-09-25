# DGX / GB10 appliance runbook

Owner: Akshay Sunil Navani (Person 2). Applies to DGX OS (Ubuntu 22.04, `aarch64`) on NVIDIA GB10 / DGX Spark-class devices.

## What gets installed

| Piece | Where | Runs as |
| --- | --- | --- |
| Runtime daemon (only component with Docker access) | `/usr/lib/crewquarters/python`, `crewquarters-runtime.{socket,service}` | `crewquarters-runtime` (in the `docker` group), Unix socket `/run/crewquarters/runtime.sock` (mode 0660, group `crewquarters`) |
| Host firewall guard | `crewquarters-netguard.service` | root, oneshot: drops host-bound traffic from `cqa-*` / `cqm-*` bridges |
| Platform stack (Postgres, control API, scheduler, model gateway, capability broker, knowledge service) | `/usr/share/crewquarters/compose/compose.appliance.yaml`, `crewquarters.service` | containers, non-root (uid 10001) plus the `crewquarters` group (`CQ_SOCKET_GID`) |
| Edge proxy and web UI | `crewquarters/proxy:<version>` image, service `proxy` | nginx, uid 101; the **only** published port (`127.0.0.1:8080`; in LAN HTTPS mode `:443` plus an HTTP redirect). Routing: [proxy.md](proxy.md) |
| Desktop launcher | `/usr/share/applications/crewquarters.desktop` -> `/usr/share/crewquarters/launch.sh` | the desktop user: waits for readiness, opens `/setup` on first use, then `/` |
| LAN HTTPS mode (opt-in) | `/etc/crewquarters/tls/` (device CA: `ca.key` 0600 root), `/etc/crewquarters/lan-https.env`, `compose.lan-https.yaml`, `/usr/lib/crewquarters/lan-tls.sh` | [lan-https.md](lan-https.md) |
| Configuration | `/etc/crewquarters/crewquarters.env` (conffile) | |
| Secrets (generated once) | `/etc/crewquarters/secrets.env`, `runtime-token`, `master.key` (0640 `root:crewquarters`) | |
| Data | `/var/lib/crewquarters/{postgres,models,runs,documents,embedding-models,backups}` | preserved on remove and purge |
| Model catalog (pinned) | `/usr/share/crewquarters/catalog/models/*.json` | read-only |

## Install

```bash
# Online: the platform image comes from the offline bundle or your registry.
sudo apt install ./crewquarters_0.1.0_arm64.deb
# Offline demo bundle (images, checksums, SBOMs, optional model):
sudo crewquarters load-bundle crewquarters-offline_0.1.0_arm64.tar.zst
sudo systemctl start crewquarters.service
sudo crewquarters bootstrap-token      # one-time owner setup code
/usr/share/crewquarters/launch.sh     # or the Crewquarters desktop launcher (waits, then opens /setup)
```

**Headless device (no display):** set it up from another computer over HTTPS. Use `sudo CREWQUARTERS_HEADLESS=yes apt install ./crewquarters_0.1.0_arm64.deb` at install time, or `sudo crewquarters lan-https enable` later. Either one prints the LAN URL, the device CA fingerprint and how to trust the CA. See [lan-https.md](lan-https.md).

The package scripts are non-interactive. They never ask for passwords or keys, never run OAuth, and never download models. On its first start, the stack's one-shot `knowledge-model` service downloads the pinned CPU embedding model (about 120 MB) and verifies it; an offline bundle built with that model pre-populates `/var/lib/crewquarters/embedding-models` instead.

## Services, networks and secrets

| Service | Networks | Host mounts | Notes |
| --- | --- | --- | --- |
| `proxy` | default, `callbacks` | none | Publishes `CQ_BIND_ADDRESS:CQ_HTTP_PORT`; `/internal/*` is 404 at the edge |
| `control-api`, `scheduler`, `migrate` | default | `/run/crewquarters` (runtime socket) | |
| `model-gateway` | default, `cq-models` | runtime socket, `master.key` (read-only) | Decrypts OpenAI/Anthropic keys |
| `capability-broker` | default, `cq-agents` (alias `capability-broker`) | `master.key` (read-only) | Agents reach it as `http://capability-broker:8000`; owns the Google/Twilio callbacks |
| `knowledge-model` (one-shot), `knowledge` | default | `documents`, `embedding-models` (both `2770 root:crewquarters`) | `cq-knowledge fetch-model`, then the service |
| `tunnel` (profile `callbacks`) | `callbacks` only | none | `crewquarters tunnel up|down`; see [proxy.md](proxy.md) |

- **Master key.** `/etc/crewquarters/master.key` is a keyring (`1:<64 hex>`), `root:crewquarters`, mode 0640. It is bind-mounted read-only into exactly two containers, `capability-broker` and `model-gateway`, as `/run/crewquarters-keys/master.key` (`CQ_MASTER_KEY_FILE`). They run as uid 10001 and read it through `group_add: CQ_SOCKET_GID`, the host `crewquarters` group that postinst records. No other service mounts it. The loader refuses a file that "other" can read.
- **Agent network.** Both external networks (`cq-models`, `cq-agents`) are created by the runtime daemon. `crewquarters up` waits for them.

## Preflight

```bash
sudo crewquarters preflight            # required: aarch64, Docker, Compose, NVIDIA runtime, GPU
sudo crewquarters preflight --gpu-test # also runs: docker run --gpus=all <cuda image> nvidia-smi -L
```

For each failed check:

| Failed check | Fix |
| --- | --- |
| `nvidia container runtime` | `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| `gpu` | Check that `nvidia-smi` works on the host, then check the driver |
| `docker compose` | Install `docker-compose-plugin` |
| `memory` / `disk` (warnings) | 128 GB class memory and at least 100 GB free are expected; lower values are allowed but limit models |

## Network isolation

- The daemon creates `cq-agents` and `cq-models` as **internal** Docker networks, using the **isolated gateway mode** available on Docker 28 and later: the bridge gets no host address.
- Agent containers can reach only the capability broker. Model containers can reach only the model gateway.
- On older Docker engines the daemon reports `firewall-required` in `GET /internal/v1/host/capacity`. `crewquarters-netguard` then drops host-bound traffic from those bridges instead: `iptables -I INPUT -i cqa-+ -j DROP` (and the same for `cqm-+`).
- To verify, run `services/runtime_daemon/tests/test_daemon.py::test_hardened_container_cannot_reach_prohibited_targets` against the device.

## Models

- The catalog pins the NGC vLLM image by digest (`nvcr.io/nvidia/vllm:25.09-py3`, multi-arch) and Hugging Face models by commit.
- Both profiles are **candidates** until they are measured on the target device (PLAN.md section 8.1). Confirm the image tag against NVIDIA's current DGX Spark vLLM recipe, run the benchmark below, and update `memory.startupPeakBytes`, `validation.status` and `--gpu-memory-utilization`.
- Downloads are staged in `/var/lib/crewquarters/models/.staging`. They check free disk first (keeping a 20 GiB reserve), resume, verify SHA-256, and are installed with an atomic rename.
- Loading goes through the gateway's admission control: 24 GiB system reserve, 96 GiB serving cap, 8 GiB margin, and one generative model by default. Tune these in `crewquarters.env`.
- An idle model unloads after `CQ_GATEWAY_IDLE_UNLOAD_SECONDS` (600 s). The daemon stops and removes the container, then samples memory to confirm the release.

## Benchmark (on the device)

```bash
sudo crewquarters bootstrap-token   # sign in once, then:
uv run python infra/scripts/benchmark_model.py --model local.general.small --runs 5 \
    --gateway http://127.0.0.1:8090 --token "$(sudo grep CQ_INTERNAL_SERVICE_TOKEN /etc/crewquarters/secrets.env | cut -d= -f2)"
```

The script records the cold-start time, the first-token latency (warm), output tokens per second, and host available memory before load, while resident, and after unload. Put the results in `docs/benchmarks/gb10.md` before the demo.

## Upgrade, rollback, uninstall

- **Upgrade:** `sudo apt install ./crewquarters_<new>_arm64.deb`. The preinst script backs up the database to `/var/lib/crewquarters/backups/pre-upgrade-<old>-<time>.sql.gz`. The migrate service applies the new migrations when the stack starts.
- **Rollback:** reinstall the previous `.deb` and set `CQ_VERSION` back in `crewquarters.env`. Database downgrade migrations are not assumed; if the new migrations must be undone, restore the pre-upgrade dump.
- **Uninstall:** `sudo apt remove crewquarters` stops the services and keeps all data. `sudo apt purge crewquarters` also removes `/etc/crewquarters` and the service users. The data directory is deleted only with `sudo CREWQUARTERS_PURGE_DATA=yes apt purge crewquarters`.

## Diagnostics

- Service status: `systemctl status crewquarters-runtime crewquarters crewquarters-netguard`
- Daemon logs: `journalctl -u crewquarters-runtime` (JSON)
- Stack status: `sudo crewquarters status` (Compose status plus preflight)
- Stack logs: `sudo crewquarters logs model-gateway`
- Metrics: control API `GET /internal/v1/metrics`, model gateway `GET /internal/v1/metrics`, scheduler `:9101/metrics`. These are not reachable through the proxy; use `sudo crewquarters logs` or `docker compose -p crewquarters exec <service> ...`.
- Proxy: `sudo crewquarters logs proxy`. The access log is JSON, without query strings.
