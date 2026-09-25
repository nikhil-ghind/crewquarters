#!/usr/bin/env bash
# Run the suites and write evidence/<UTC timestamp>/report.md plus JUnit XML and logs.
# The Docker E2E suite runs only when the dev stack is up (make dev-up).
set -uo pipefail
cd "$(dirname "$0")/../.."
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="evidence/$stamp"
mkdir -p "$out"
run="uv run --all-packages"

$run pytest -m "not e2e and not live" --junitxml="$out/junit-test.xml" >"$out/test.log" 2>&1
test_status=$?
{ $run ruff check . && $run ruff format --check . && $run mypy; } >"$out/lint.log" 2>&1
lint_status=$?
if curl -fsS "${CREWQ_FAKE_URL:-http://127.0.0.1:8080}/health/ready" >/dev/null 2>&1; then
  if make e2e-images >"$out/e2e-build.log" 2>&1; then
    $run pytest -m e2e tests/e2e --junitxml="$out/junit-e2e.xml" >"$out/e2e.log" 2>&1
    e2e_status=$?
  else
    e2e_status=build-failed
  fi
else
  e2e_status=skipped
fi

$run python infra/scripts/evidence_report.py "$out" \
  --suite "unit + contract + integration=$out/junit-test.xml=$test_status" \
  --suite "lint + format + types==$lint_status" \
  --suite "docker e2e=$out/junit-e2e.xml=$e2e_status"
echo "Evidence written to $out/report.md"
[ "$test_status" = 0 ] && [ "$lint_status" = 0 ] && { [ "$e2e_status" = 0 ] || [ "$e2e_status" = skipped ]; }
