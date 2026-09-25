#!/bin/sh
# Crewquarters desktop launcher (unprivileged; PLAN.md sections 13.4 and 14.2).
# Waits until the platform answers, then opens the setup wizard (/setup) the first time
# and the dashboard (/) afterwards. The UI itself resumes an unfinished setup, so "/" is
# always safe. Works in both modes: the address comes from /var/lib/crewquarters/public/
# ui-url, which `crewquarters` keeps current (http://localhost:8080, or https://NAME.local
# in LAN HTTPS mode, verified against the device CA in the same directory).
#
# Environment (all optional):
#   CREWQUARTERS_URL           base address, overriding ui-url (e.g. https://spark.local)
#   CREWQUARTERS_WAIT_SECONDS  how long to wait for the platform (default 180)
#   CREWQUARTERS_PUBLIC_DIR    where ui-url and ca.crt are (default /var/lib/crewquarters/public)
set -u

PUBLIC_DIR="${CREWQUARTERS_PUBLIC_DIR:-/var/lib/crewquarters/public}"
WAIT="${CREWQUARTERS_WAIT_SECONDS:-180}"
STATE_DIR="${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/crewquarters"
MARKER="$STATE_DIR/setup-opened"

BASE="${CREWQUARTERS_URL:-}"
[ -n "$BASE" ] || BASE=$(head -n 1 "$PUBLIC_DIR/ui-url" 2>/dev/null || true)
[ -n "$BASE" ] || BASE=http://localhost:8080
BASE=${BASE%/}

say() {
    echo "$1"
    # Launched from the desktop there is no terminal: use a notification when possible.
    if [ ! -t 1 ] && command -v notify-send >/dev/null 2>&1; then
        notify-send --app-name=Crewquarters "Crewquarters" "$1" >/dev/null 2>&1 || true
    fi
}

scheme=${BASE%%://*}
hostport=${BASE#*://}
hostport=${hostport%%/*}
host=${hostport%:*}
port=${hostport##*:}
[ "$port" != "$hostport" ] || { [ "$scheme" = https ] && port=443 || port=80; }

# HTTPS is verified against the device CA when it is available.
tls_opts=""
if [ "$scheme" = https ]; then
    if [ -f "$PUBLIC_DIR/ca.crt" ]; then tls_opts="--cacert $PUBLIC_DIR/ca.crt"; else tls_opts="--insecure"; fi
fi

# The HTTP status of BASE+PATH ("000" when nothing answers). If this computer cannot
# resolve the device's own name (no mDNS), ask 127.0.0.1 for that name instead.
resolve=""
probe() {
    # shellcheck disable=SC2086  # tls_opts/resolve are option lists
    curl -s -o /dev/null -w '%{http_code}' --max-time 5 $tls_opts $resolve "$BASE$1" 2>/dev/null || true
}

is_ip() { printf '%s' "$1" | grep -Eq '^[0-9.]+$|:'; }
if ! is_ip "$host" && [ "$host" != localhost ] && ! getent hosts "$host" >/dev/null 2>&1; then
    resolve="--resolve $host:$port:127.0.0.1"
    # The browser on this computer cannot resolve it either: open https://localhost, which
    # the device certificate also covers. (Google sign-in needs the configured name.)
    OPEN_BASE="$scheme://localhost$([ "$port" = 443 ] || [ "$port" = 80 ] || echo ":$port")"
else
    OPEN_BASE=$BASE
fi

code=$(probe /api/v1/health/ready)
if [ "$code" != 200 ]; then
    say "Crewquarters is starting. The page opens as soon as it is ready; after a reboot this can take a few minutes."
    waited=0
    while [ "$waited" -lt "$WAIT" ]; do
        sleep 2
        waited=$((waited + 2))
        code=$(probe /api/v1/health/ready)
        [ "$code" = 200 ] && break
    done
fi
if [ "$code" = 000 ]; then
    say "Crewquarters is not running at $BASE. Start it with: sudo systemctl start crewquarters (status: sudo crewquarters status)"
    exit 1
fi
if [ "$code" != 200 ]; then
    # The proxy answers but the API is not ready: the UI shows its own progress/error.
    say "Crewquarters is still starting; opening it anyway."
fi

if [ -f "$MARKER" ]; then path=/; else path=/setup; fi
URL="$OPEN_BASE$path"
if command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL"; then
    mkdir -p "$STATE_DIR" && : > "$MARKER"
    exit 0
fi
say "Open $URL in a browser."
mkdir -p "$STATE_DIR" && : > "$MARKER"
