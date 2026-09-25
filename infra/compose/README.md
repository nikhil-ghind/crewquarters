# Laptop Compose (`dev` profile)

| Service | Port | Purpose |
| --- | --- | --- |
| `fake-platform` | `127.0.0.1:8080` | Fake control-API slice, broker, model gateway, knowledge, Gmail/Sheets/Twilio (`/fake/v1` admin API for tests) |
| `registry` | `127.0.0.1:5001` | Local OCI registry so agents run pinned by digest, just as the real daemon will run them |

Networks:

- `crewq-control`: the fake platform and the registry.
- `crewq-agents`: `internal: true`, so it has no internet egress. Agent containers attach here and
  reach only the alias `broker`, which is the fake platform.

The fake platform mounts the repository read-only at `/workspace` and resolves scenario paths
relative to it (for example `tests/fixtures/scenarios/demo`).

For a realistic local model, point the fake's `local.general.*` profiles at any OpenAI-compatible
server before `make dev-up`:

```bash
export CREWQ_FAKE_LLM_BASE_URL=http://host.docker.internal:11434/v1   # e.g. Ollama
export CREWQ_FAKE_LLM_MODEL=llama3.2:3b
```

Boundary with Person 2 (`dgx` profile, host daemon, `.deb`): this file owns only the `dev`
profile. Persons 1–3 add their real services to the default profile and remove the matching fake
routes as they land.
