# Release security checklist (PLAN.md §16.2, §23.6, §23.8)

This is the security signoff for a release. Each PLAN §16.2 check is listed with how it is
verified, the evidence (CI job, test, or script), and its status as of 2026-09-25 (commit
`176e376` plus the branch that added this file). A reviewer signs off at the end, and the
signoff records each open risk as either accepted or blocking.

Status values: **Pass**, where automated evidence runs in CI and passes. **Pass (manual)**,
where the evidence exists but a person runs it. **Partial**, where some of the check is
automated and the gap is named. **Open**, where the check is not implemented.

Where to find the evidence: the CI jobs in `.github/workflows/ci.yml`. Image SBOMs and
vulnerability reports are the CI artifacts `image-security-<image>-<arch>`, kept for 90 days.
To reproduce the image checks locally, build an image archive and run the same scripts:

```sh
docker buildx build --platform linux/arm64 -f infra/docker/python.Dockerfile \
  --output type=docker,dest=/tmp/platform.tar .
python3 infra/scripts/check_image.py /tmp/platform.tar
infra/scripts/scan_secrets.sh --image /tmp/platform.tar
docker run --rm -v /tmp:/work aquasec/trivy:0.66.0 image --input /work/platform.tar \
  --severity HIGH,CRITICAL
```

## Severity policy (vulnerabilities)

| Finding | Action |
| --- | --- |
| CRITICAL with a fixed version | **Blocks the build** (`image-security`, `dependency-scan`). Fix it by bumping the base-image digest or the package. An exception goes in `infra/scripts/trivyignore` with a reason and an `exp:` date, reviewed by the security owner (Person 3). |
| CRITICAL with no fix yet | Reported in the job summary and artifact. Triaged at each release and listed under "Open risks" below. |
| HIGH | Reported, not blocking. Triaged at each release. |
| Secret in a built artifact | **Blocks the build**. An allowlist entry in `infra/scripts/trivy-secret.yaml` needs a stated reason and the security owner's review. |

## PLAN §16.2 checks

| # | Check | How it is verified | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1 | Dependency and container vulnerability scan with a documented severity policy | Trivy scans `uv.lock` and `apps/web/package-lock.json`, plus the full filesystem of every image on both architectures. The policy is above. | CI `dependency-scan`; CI `image-security` (10 matrix jobs: platform, proxy, contract_probe, gmail_digest, caller × amd64, arm64), step "Vulnerability scan (fail on fixable CRITICAL)" | **Pass**. Scan results for 2026-09-25 are below. |
| 2 | SBOM for every core and bundled-agent image | Syft writes an SPDX 2.3 JSON SBOM from each image archive, for both architectures. The offline bundle also embeds SBOMs when syft is installed (`build-offline-bundle.sh`). | CI `image-security`, step "SBOM (SPDX JSON)", artifact `image-security-<image>-<arch>/sbom-<image>-<arch>.spdx.json` | **Pass** |
| 3 | Secret scanner on the repository and on built artifacts | Gitleaks scans the repository with full history. Trivy's secret scanner covers the `.deb` (data and control members), the web UI bundle (`apps/web/dist`), every image filesystem, and the offline bundle's manifest (`SHA256SUMS`), SBOMs and `.deb`. A planted AWS key and private key fail the scan, which was verified locally. | CI `secret-scan` (repository). CI `package`, step "Secret scan of the built packages". CI `web`, step "Secret scan of the built UI". CI `image-security`, step "Secret scan of the image filesystem". CI `offline-bundle-secrets` (main and tags). Script `infra/scripts/scan_secrets.sh` | **Pass**. No findings, and the allowlist is empty. |
| 4 | No mutable image tags in release manifests | `check_release_refs.py` renders `compose.appliance.yaml` (all profiles) with the release env and checks the model catalog, the bundled agent manifests and the Dockerfile `FROM` lines. Release mode fails on any tag-only reference; dev mode warns. A self-test proves that release mode rejects tags and accepts `CQ_VERSION=<v>@sha256:<digest>`. | CI `release-refs` (the self-test runs on every PR; the full release-mode check runs on `v*` tags against `release.env`) | **Partial**. The appliance Compose file and the catalog pass once the release pins `CQ_VERSION` by digest. `infra/docker/python.Dockerfile` still uses `python:3.12-slim-bookworm` and `ghcr.io/astral-sh/uv:0.5.11` by tag, so the full release check fails until they are pinned (owner: Person 2). |
| 5 | Negative authorization tests for every broker capability | Every capability operation is called without its capability, with a revoked approval, with a stale attempt, and on an inactive run. Tokens are also tested missing, forged, expired and replaced. Fuzzed tokens and signed-but-malformed claims are rejected as invalid tokens. | `services/capability_broker/tests/test_broker_auth.py` (`test_every_capability_operation_requires_its_capability` and 13 others); `tests/fuzz/test_fuzz_capability.py` (CI `platform-tests`, `fuzz`) | **Pass** |
| 6 | Container access attempts to the host, database, model endpoint, metadata/host gateway and internet fail | The contract-probe agent runs in the hardened container on the internal network and checks: it is not root, the root filesystem is read-only, and it cannot connect to 1.1.1.1:443, `host.docker.internal:80` or example.com:443. `check_image.py` verifies non-root `Config.User` and the setuid allowlist for every image. | CI `agent-e2e` (`tests/e2e/test_e2e_agents.py::test_contract_probe_passes_every_check_in_the_hardened_container`, `test_images_run_as_non_root_and_self_check`); CI `image-security` | **Partial**. The probe does not yet try PostgreSQL (`postgres:5432`), the vLLM endpoint on `cq-models`, the cloud metadata address `169.254.169.254`, or the Docker socket. It runs against the fake platform, not the real daemon on GB10 (owners: Person 2, Person 5). |
| 7 | Fuzz and limit tests for manifests, uploads, webhook bodies, run events and JSON Schema inputs | Hypothesis properties run with a bounded, derandomized budget (`CQ_FUZZ_EXAMPLES`, default 150). They cover manifests, config schemas and forms, run events and redaction, cron, capability tokens, A1 ranges, Twilio callbacks (signature, body size, forged or unknown calls, TwiML escaping), document extraction in the isolation process, Gmail MIME parsing, and control API bodies (oversized, chunked, malformed, deeply nested, NUL, hostile paths and queries). | CI `fuzz` (`tests/fuzz/`) | **Pass**. Three crashes were found and fixed (see "Fuzz findings"). |
| 8 | Browser security headers and CSRF/Origin tests | The control API tests CSRF and Origin enforcement and the `nosniff` header. The proxy's headers come from `infra/proxy/security-headers.conf`, which `nginx -t` validates in CI. The UI's Playwright suite runs under a CSP mock. | `services/control_api/tests/test_auth.py::test_state_change_requires_csrf_and_origin`, `test_service_integrations.py::test_connection_changes_need_csrf`; CI `package` "Proxy config and routing"; CI `web` Playwright | **Partial**. No automated test asserts the proxy's response headers (CSP, frame-ancestors, HSTS policy) on a running proxy (owner: Person 4). |

## §23.6 image requirements

| Requirement | Evidence | Status |
| --- | --- | --- |
| Non-root | `check_image.py` reads `Config.User` from every image archive without running it. platform and agents run as `10001:10001`, proxy as `101`. | **Pass**, all 5 images × 2 architectures |
| Pinned by digest | `check_release_refs.py` (check 4). Agent images are pinned by digest in their manifests by `crewctl build --push`. | **Partial** (see check 4) |
| Scanned | Check 1 | **Pass** |
| SBOM | Check 2 | **Pass** |
| No setuid/setgid binaries beyond an allowlist | `check_image.py` with `infra/scripts/setuid-allowlist.txt` | **Pass with allowlist**. The Debian base's `su`, `passwd`, `mount` and similar binaries are allowlisted because the images run non-root and agent/model containers run with no-new-privileges. Follow-up: strip them in the Dockerfiles (Person 2, Person 5). The proxy has none. |

## Scan results (2026-09-25, local, amd64, Trivy 0.66.0)

| Image | CRITICAL | HIGH | Fixable CRITICAL | Setuid (allowlisted) | Secrets |
| --- | --- | --- | --- | --- | --- |
| platform | 5 | 55 | 0 | 11 | 0 |
| proxy | 0 | 1 | 0 | 0 | 0 |
| contract_probe | 0 | 44 | 0 | 11 | 0 |
| gmail_digest | 0 | 44 | 0 | 11 | 0 |
| caller | 0 | 44 | 0 | 11 | 0 |

- **proxy**: the previous base (`nginx-unprivileged:1.28-alpine@sha256:7377…`, Alpine 3.23.3)
  had 3 fixable CRITICAL findings (OpenSSL CVE-2026-31789 in libssl3 and libcrypto3, and nginx
  CVE-2026-42945) and 60 fixable HIGH. The base is now
  `nginx-unprivileged:1.28.3-alpine@sha256:6a23…` (Alpine 3.24.2), which leaves 1 HIGH.
  `nginx -t` passes. The same image was checked on arm64 with the same result.
- **platform**: its 5 CRITICAL findings have no Debian fix: libsqlite3-0 CVE-2025-7458,
  perl-base CVE-2026-13221, CVE-2026-42496 and CVE-2026-8376, and zlib1g CVE-2023-45853
  (`will_not_fix`, the minizip API, which is not used). None is reachable from the services'
  code paths. Re-check them at each release.
- The agent images' HIGH findings are all in Debian base packages with no fixed version.
- The lockfiles (`uv.lock`, `package-lock.json`) have no HIGH or CRITICAL findings.

## Fuzz findings (fixed, with regression tests)

1. **Deeply nested JSON caused a 500 on the control API.** The case is JSON nested a few
   hundred levels deep (about 400 in testing), which fits in a 16 KiB event payload. `redact()`, JSON Schema validation
   and response serialization recurse once per level. The fix is
   `crewquarters_api/json_guard.py`: 422 when JSON nests deeper than 64 levels. Tests:
   `tests/fuzz/test_fuzz_control_api_limits.py::test_deeply_nested_*`.
2. **A NUL character (`\u0000`) in a JSON string or a path/query parameter caused a 500.**
   PostgreSQL's `jsonb` and `text` cannot store it. The same guard returns 422. Tests:
   `test_nul_characters_are_rejected_not_stored` and `test_hostile_action_keys_are_not_a_500`.
3. **A signed capability token with missing or wrongly typed claims caused a 500.** For
   example, a token without `run` raised `KeyError`, and a string `cap` was split into
   characters. `capability.verify` now raises `jwt.InvalidTokenError`, which is a 401. Test:
   `tests/fuzz/test_fuzz_capability.py::test_regression_signed_malformed_claims_are_invalid_tokens`.

Noted, not changed: the SDK's `html_to_text` lets a `<script>` body through as plain text when
malformed markup such as `<?` precedes it. The output is model input, never rendered HTML, so
this is not a vulnerability.

## Licenses (§23.1)

`THIRD_PARTY_LICENSES.md` is generated by `infra/scripts/license_inventory.py` and checked for
drift in CI `lint`. It and `NOTICE` are installed by the `.deb` in `/usr/share/doc/crewquarters/`
(checked in CI `package`). The inventory's "Needs review" section lists these items for a
decision:

| Item | License | Proposed decision |
| --- | --- | --- |
| psycopg, psycopg-binary | LGPL-3.0-only | Accept. The packages are unmodified and dynamically imported, and are attributed in NOTICE. |
| certifi, tqdm | MPL-2.0 | Accept. The files are unmodified and attributed, with the upstream source linked. |
| Debian and Alpine base packages (GPL/LGPL) | various | Accept as mere aggregation. The per-package list is in the SBOMs, and the source is available from the distribution archives. |
| NVIDIA vLLM container | NVIDIA Deep Learning Container License | Review before redistributing it in an offline bundle (`--with-vllm`). Pulling it from NGC on the appliance is the default. |
| Xenova/jina-embeddings-v2-small-en | No license in the repository; the base model is Apache-2.0 | Accept with attribution to Jina AI in NOTICE, or switch to an export that carries the license. |
| Crewquarters' own code | No `LICENSE` file | **Blocking for a public release**. The project owner must choose a license. |

## Open risks for signoff

| Risk | Owner | Blocks the demo? |
| --- | --- | --- |
| Platform Dockerfile bases are pinned by tag, not digest (check 4) | Person 2 | Blocks the release check. Pin both bases by digest. |
| Isolation probe does not try the DB, vLLM, metadata or Docker socket targets (check 6) | Person 2, Person 5 | Should, per §23.6 Must "Agent cannot access Docker, DB, vLLM directly..." |
| Proxy security headers are not asserted by a test (check 8) | Person 4 | Should |
| Allowlisted setuid binaries in the Debian-based images | Person 2, Person 5 | No (mitigated by non-root and no-new-privileges) |
| Unfixed CRITICAL findings in the platform base (Debian) | Person 2 | No (no fix available, not reachable) |
| The broker and model gateway do not apply the control API's JSON depth/NUL guard | Person 3, Person 2 | No. Their inputs are forwarded to the control API, which rejects them, but they were not fuzzed at the HTTP layer. |
| Agent images install SDK dependencies with `pip` without a lock or hashes | Person 5 | No. The versions are recorded in the SBOMs. Use a lock and `--require-hashes` for a release. |

## Signoff

| Role | Name | Date | Decision (accept / block) and notes |
| --- | --- | --- | --- |
| Security owner (Person 3) | | | |
| Release/integration lead (Person 1) | | | |
