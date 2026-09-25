#!/bin/sh
# Desktop launcher: opens the local setup UI (unprivileged; PLAN.md section 14.2).
URL="${CREWQUARTERS_URL:-http://localhost:8080/setup}"
if command -v xdg-open >/dev/null 2>&1; then exec xdg-open "$URL"; fi
echo "Open $URL in a browser."
