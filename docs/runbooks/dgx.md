# DGX / GB10 appliance runbook

Owner: Akshay Sunil Navani (Person 2). Applies to DGX OS (Ubuntu 22.04, `aarch64`) on NVIDIA GB10 / DGX Spark-class devices.

## What gets installed

| Piece | Where | Runs as |
| --- | --- | --- |
| Runtime daemon (only component with Docker access) | `/usr/lib/crewquarters/python`, `crewquarters-runtime.{socket,service}` | `crewquarters-runtime` (in the `docker` group), Unix socket `/run/crewquarters/runtime.sock` (mode 0660, group `crewquarters`) |
| Host firewall guard | `crewquarters-netguard.service` | root, oneshot: drops host-bound traffic from `cqa-*` / `cqm-*` bridges |
| Platform stack (Postgres, control API, scheduler, model gateway) | `/usr/share/crewquarters/compose/compose.appliance.yaml`, `crewquarters.service` | containers, non-root (uid 10001) plus the `crewquarters` group for the socket |
| Configuration | `/etc/crewquarters/crewquarters.env` (conffile) | |
| Secrets (generated once) | `/etc/crewquarters/secrets.env`, `runtime-token`, `master.key` (0640) | |
| Data | `/var/lib/crewquarters/{postgres,models,runs,documents,backups}` | preserved on remove and purge |
| Model catalog (pinned) | `/usr/share/crewquarters/catalog/models/*.json` | read-only |

## Install

```bash
# Online: the platform image comes from the offline bundle or your registry.
sudo apt install ./crewquarters_0.1.0_arm64.deb
# Offline demo bundle (images, checksums, SBOMs, optional model):
sudo crewquarters load-bundle crewquarters-offline_0.1.0_arm64.tar.zst
sudo systemctl start crewquarters.service
sudo crewquarters bootstrap-token      # one-time owner setup code
xdg-open http://localhost:8080/setup   # or the Crewquarters desktop launcher
```

The package scripts are non-interactive. They never ask for passwords or keys, never run OAuth, and never download models.

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
- Metrics: control API `GET /internal/v1/metrics`, model gateway `GET /internal/v1/metrics`, scheduler `:9101/metrics`
