#!/usr/bin/env bash
# Between rehearsals: clear demo runs, calls, and result rows and reseed the fixture sheets and
# mailbox without touching images (PLAN.md section 24, final paragraph).
set -euo pipefail
exec "$(dirname "$0")/demo-seed.sh" "$@"
