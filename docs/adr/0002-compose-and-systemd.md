# 0002. Docker Compose and a systemd host daemon, not Kubernetes, for v1

- Status: Accepted
- Date: 2026-09-24
- Owners: Nikhil Hiro Ghind (Person 1), Akshay Sunil Navani (Person 2)

## Context

v1 runs on one owner-operated GB10 appliance (`arm64`, DGX OS) and on developer laptops. DGX OS ships with Docker and the NVIDIA Container Toolkit already configured. K3s supports `arm64`, but GPU runtime classes, persistent volumes, upgrades, and networking would all need separate appliance validation. The hardest security boundary, running agent code, depends on runtime policy (capabilities, seccomp, network, mounts), not on which scheduler is used.

## Decision

- **Docker Compose** runs the application services: reverse proxy/UI, control API, scheduler/worker, capability broker, knowledge service, model gateway, and PostgreSQL/pgvector. `infra/compose/compose.yaml` is the core stack. Profiles add `dev`, `dgx`, and `callbacks`.
- **A systemd runtime daemon** is the only process on the host that can use Docker. It listens on a Unix socket, not TCP. It starts per-run agent containers and on-demand vLLM containers from validated platform records, and never accepts raw `docker run` payloads.
- Every application image is built for `linux/amd64` and `linux/arm64`. vLLM uses the NVIDIA-recommended GB10 image instead.
- Health checks gate service start order (`depends_on: condition: service_healthy / service_completed_successfully`). Migrations run once as a one-shot `migrate` service.
- Kubernetes/k3s work does not start in v1 unless Compose is shown to be impossible on the exact appliance.

## Consequences

- The installer is smaller, debugging is easier, and the laptop demo is simpler.
- High availability and multi-device scheduling are not available. That is acceptable for a single-owner appliance.
- After the demo, revisit this decision (new ADR) if at least two of these appear: multi-device scheduling, service HA, many long-running agents, team-standard Kubernetes operations, or a Kubernetes-native sandbox (PLAN.md section 14.4). Multi-node vLLM is a separate project either way.
