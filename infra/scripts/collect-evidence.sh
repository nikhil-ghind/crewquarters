#!/usr/bin/env bash
# Run the suites and write evidence/<UTC timestamp>/report.md plus JUnit XML and logs.
# The full suite needs PostgreSQL (make db-up); without Docker only the DB-free tests run.
# The Docker E2E suite runs only when the fake platform is up (make fake-up).
set -uo pipefail
cd "$(dirname "$0")/../.."
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="evidence/$stamp"
mkdir -p "$out"
run="uv run --all-packages"

if make db-up >"$out/db.log" 2>&1; then
  markers="not e2e and not live"
  suite_name="control plane + SDK + agents + contract + integration"
else
  markers="no_db and not e2e and not live"
  suite_name="DB-free suites only (PostgreSQL unavailable)"
fi
$run pytest -m "$markers" --junitxml="$out/junit-test.xml" >"$out/test.log" 2>&1
test_status=$?
make lint >"$out/lint.log" 2>&1
lint_status=$?
if curl -fsS "${CREWQ_FAKE_URL:-http://127.0.0.1:8090}/health/ready" >/dev/null 2>&1; then
  if make e2e-images >"$out/e2e-build.log" 2>&1; then
    CREWQ_E2E_PLATFORM_URL="${CREWQ_FAKE_URL:-http://127.0.0.1:8090}" \
      $run pytest -m e2e tests/e2e --junitxml="$out/junit-e2e.xml" >"$out/e2e.log" 2>&1
    e2e_status=$?
  else
    e2e_status=build-failed
  fi
else
  e2e_status=skipped
fi

$run python infra/scripts/evidence_report.py "$out" \
  --suite "$suite_name=$out/junit-test.xml=$test_status" \
  --suite "lint + format + types==$lint_status" \
  --suite "docker e2e=$out/junit-e2e.xml=$e2e_status"
echo "Evidence written to $out/report.md"
[ "$test_status" = 0 ] && [ "$lint_status" = 0 ] && { [ "$e2e_status" = 0 ] || [ "$e2e_status" = skipped ]; }
