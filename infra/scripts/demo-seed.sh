#!/usr/bin/env bash
# Reset the fake platform and load the demo scenario. Runs, calls, sheets, faults, and answers are
# cleared; agent images in the local registry are kept. Usage: demo-seed.sh [scenario-path]
set -euo pipefail
cd "$(dirname "$0")/../.."
url="${CREWQ_FAKE_URL:-http://127.0.0.1:8090}"
scenario="${1:-tests/fixtures/scenarios/demo}"
uv run --all-packages crewq-fake reset --url "$url"
uv run --all-packages crewq-fake seed "$scenario" --url "$url"
