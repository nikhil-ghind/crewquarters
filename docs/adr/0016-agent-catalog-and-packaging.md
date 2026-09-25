# ADR 0016: Curated local catalog and digest-pinned agent packages

- Status: Accepted
- Date: 2026-09-25
- Owner: Vineet Kumar (Person 5), with Nikhil Hiro Ghind (Person 1) for the catalog and installation tables

## Context

Crewquarters is described as a local agent marketplace, but v1 serves one owner on one device (PLAN.md sections 1 and 2.2). Agents are containers the owner did not write. Docker is not a hostile-code sandbox (PLAN.md section 25, "Arbitrary agent security"). The platform therefore has to know exactly which image runs and exactly what the owner allowed it to do. It also has to notice when either changes.

## Decision

**A curated local catalog, not a public marketplace.** There is no remote catalog service, ratings, payments or public listing. Agents enter the catalog in two ways, both through `services/control_api/src/crewquarters_api/catalog.py`:

- **Bundled.** At startup the control API loads every `*.yaml` in `CQ_CATALOG_DIR` with `sync_directory()`, and `cq-admin catalog-sync` does the same. These entries get `source=bundled` and `trust_status=curated`.
- **Imported.** An owner session calls `POST /api/v1/catalog/agents/import` (`routers/agents.py`, `import_agent`). This is what `crewctl publish --target local` does. These entries get `source=imported`, `trust_status=imported_unreviewed`, and an audited `catalog.import` event with the digest.
- An agent ID cannot move between sources (`409 CATALOG_SOURCE_CONFLICT`).

**One manifest schema.** `packages/contracts/agent-manifest.schema.json` (`apiVersion: crewquarters/v1alpha1`) is the only definition.

- `crewquarters_shared.manifest.validate_manifest()` applies it on import and on sync. It also checks the configuration and result schemas and rejects unknown `local.*` profiles.
- The manifest declares `image`, `entrypoint`, `architectures` (`linux/amd64`, `linux/arm64`), `triggers`, `resources`, `configurationSchema`, `resultSchema` and `permissions`.
- `permissions` covers `llmProfiles`, `knowledge`, `connectors.google`, `connectors.twilio`, `cloudProviders` and `userInput`.

**Images are pinned by digest.**

- The schema's `spec.image` pattern requires `name[:tag]@sha256:<64 hex>`, so a tag-only reference is `422 INVALID_MANIFEST`.
- The runtime daemon checks again. `require_immutable_image()` in `services/runtime_daemon/src/crewquarters_runtime/specs.py` raises `MUTABLE_IMAGE` for any run spec, model profile or `POST /internal/v1/images/pull` without a digest.
- `start_run` refuses an agent whose declared `architectures` omit the host (`ARCH_UNSUPPORTED`). It pulls the image by digest on first use.

**Versions are immutable.**

- `agent_versions` is unique on `(agent_id, version)` and stores the full normalized manifest, `image_ref`, `image_digest`, `sdk_protocol` and `architectures`.
- `upsert_manifest()` treats re-importing an identical manifest as a no-op. The same version with any difference, including a new digest, is `409 AGENT_VERSION_IMMUTABLE`. A change needs a new version number.

**Packaging with crewctl** (`packages/crewctl/src/crewctl/`):

- `crewctl init` scaffolds an agent.
- `crewctl validate` applies the same manifest rules as the control API. It rejects the `@sha256:REQUIRED_DIGEST` placeholder unless `--allow-unbuilt` is given, and checks that a `python -m` entrypoint module exists.
- `crewctl build` runs `docker buildx build`. With `--push` it builds `linux/amd64,linux/arm64` by default, reads `containerimage.digest` from the build metadata, and rewrites the manifest's `image:` line to `repo@sha256:…`. Without `--push` the manifest stays unpinned.
- `crewctl publish --target local` runs strict validation, signs in as the owner when `--username` is given (session cookie, CSRF token and `Origin` header), and posts the manifest to the import route. `local` is the only target.

**Install approves exact permissions.**

- `POST /api/v1/agent-installations` requires `approvedPermissions` equal to the version's `spec.permissions`, compared after sorting (`permissions_match()`). Anything else is `422 PERMISSIONS_NOT_APPROVED`, so the owner cannot approve a subset or a superset.
- Install also checks architecture and SDK protocol (`409 INCOMPATIBLE_AGENT`), model bindings and configuration. It audits `agent.installed` with the version, digest and derived capability list.

**Updates with changed permissions need reapproval.**

- `PATCH /api/v1/agent-installations/{id}` with a new `agentVersion` switches the installation. If the new version's permissions differ from `approved_permissions`, the installation gets `needs_reapproval = true`.
- Readiness then shows "This version requests different permissions". Dispatch fails new runs with `INSTALLATION_NOT_READY` (`services/scheduler/src/crewquarters_scheduler/worker.py`), and the scheduler skips its schedules with a `schedule.skipped` audit event.
- A later PATCH with matching `approvedPermissions` sets `approved_version_id` and clears the flag.

**Capability tokens follow the approval.** A run copies `approved_permissions` into `permissions_snapshot` when it is created (`crewquarters_shared/runs/service.py`). The worker mints the run token from that snapshot with `capabilities_from_permissions()` (ADR 0007). The broker and gateway check each call against those strings, so an agent can never use a permission the owner did not approve.

**No image signing in v1.** Trust rests on the digest, on the curated or owner-run import, and on container isolation. Signature verification (for example Sigstore/cosign), review and stronger sandboxes are future work (PLAN.md section 25).

## Alternatives considered

- **A hosted marketplace backend.** Deferred (PLAN.md section 1). It needs publisher identity, review and payments that v1 does not have.
- **Accept tags and resolve them at install.** Rejected: the image could change after the owner approved it, and two devices could run different code for the same version.
- **Per-permission approval, where the owner unticks items.** Rejected for v1: agents would need to handle every partial grant. Exact match is simpler and easier to explain.
- **Sign images now.** Deferred: it needs key distribution and a verifier in the runtime daemon. The digest already guarantees integrity after import.

## Consequences

- Anyone with the owner's password can import an agent, and it runs with whatever the owner approves. `imported_unreviewed` is shown, but nothing blocks it.
- A digest proves the image did not change. It does not prove who built it or what it does.
- Fixing a manifest mistake means publishing a new version.
- Only a change in permissions triggers reapproval. A new version with the same permissions and a new digest takes effect without asking the owner again.
- Divergences from PLAN.md:
  - Both Compose stacks set `CQ_CATALOG_DIR=/app/catalog/dev`, which holds only the development `hello-crew` manifest with a placeholder digest. The caller and Gmail digest agents reach the catalog through `crewctl publish`, so they appear as `imported_unreviewed`, not `curated` (PLAN.md sections 2.1 and 16.1).
  - PLAN.md section 13.6 describes pulling the image during install. The install route only records the version, and the runtime daemon pulls the image at the first run.
  - Architecture compatibility uses the manifest's declared `architectures`. The image's actual platforms are not inspected at import.
