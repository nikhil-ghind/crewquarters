#!/bin/bash
# Install-test the .deb inside ubuntu:22.04 (DGX OS base). Run by CI and by hand:
#   docker run --rm -v "$PWD/dist:/pkgs:ro" -v /var/run/docker.sock:/var/run/docker.sock \
#     -v "$PWD/infra/debian/test-install.sh:/t.sh:ro" ubuntu:22.04 bash /t.sh
# Exits non-zero on any failed expectation. Covers: install, permissions and secrets, the
# packaged runtime daemon, the desktop launcher (first run -> /setup, then /, readiness
# wait), LAN HTTPS mode (device CA, certificate, env overrides, launcher over HTTPS,
# disable), headless install, remove/purge/reinstall.
set -e
export DEBIAN_FRONTEND=noninteractive
DEB="${DEB:-$(ls /pkgs/crewquarters_*_amd64.deb | head -n 1)}"
fail() { echo "FAIL: $*"; exit 1; }
apt-get update -qq >/dev/null
apt-get install -y -qq "$DEB" > /tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; }
echo "== postinst output"; grep -E "^crewquarters|Crewquarters|Open the|One-time|WARNING" /tmp/install.log | head -8
grep -q 'Open the setup UI: *http://localhost:8080/setup' /tmp/install.log || fail "postinst setup URL"
echo "== python: $(python3 --version)"
echo "== version: $(crewquarters version)"
id crewquarters-runtime
echo "== perms"; stat -c '%a %U:%G %n' /etc/crewquarters /etc/crewquarters/secrets.env /etc/crewquarters/runtime-token /etc/crewquarters/master.key /var/lib/crewquarters/models /var/lib/crewquarters/postgres
echo "master.key bytes: $(stat -c %s /etc/crewquarters/master.key)"
grep -Eq '^1:[0-9a-f]{64}$' /etc/crewquarters/master.key || fail "master.key is not a keyring"
# Readable by the crewquarters group (broker + gateway containers via group_add), never by others.
[ "$(stat -c '%a %U:%G' /etc/crewquarters/master.key)" = "640 root:crewquarters" ] || fail "master.key perms"
[ "$(sed -n 's/^CQ_SOCKET_GID=//p' /etc/crewquarters/secrets.env)" = "$(stat -c %g /etc/crewquarters/master.key)" ] || fail "CQ_SOCKET_GID is not the master.key group"
for d in documents embedding-models; do
    [ "$(stat -c '%a %U:%G' /var/lib/crewquarters/$d)" = "2770 root:crewquarters" ] || fail "$d perms: $(stat -c '%a %U:%G' /var/lib/crewquarters/$d)"
done
echo "== knowledge dirs: 2770 root:crewquarters"
test -f /usr/share/crewquarters/compose/compose.appliance.yaml || fail "compose file"
test -f /usr/share/crewquarters/compose/compose.lan-https.yaml || fail "LAN HTTPS compose override"
grep -q 'crewquarters/proxy' /usr/share/crewquarters/compose/compose.appliance.yaml || fail "proxy service"
grep -q '^CQ_TWILIO_CALLBACK_BASE_URL=' /etc/crewquarters/crewquarters.env || fail "CQ_TWILIO_CALLBACK_BASE_URL in crewquarters.env"
# ubuntu images drop /usr/share/doc on install, so check the package itself.
dpkg-deb -c "$DEB" | grep -q "usr/share/doc/crewquarters/lan-https.md" || fail "LAN HTTPS runbook"
grep -c '^CQ_' /etc/crewquarters/secrets.env
grep '^CQ_SOCKET_GID=' /etc/crewquarters/secrets.env
grep -q '^CQ_CHAT_CLIENT_TOKEN=' /etc/crewquarters/secrets.env || fail "chat token"
grep -q '^CQ_VOICE_CLIENT_TOKEN=' /etc/crewquarters/secrets.env || fail "voice token"
[ "$(cat /etc/crewquarters/runtime-token)" = "$(sed -n 's/^CQ_INTERNAL_SERVICE_TOKEN=//p' /etc/crewquarters/secrets.env)" ] || fail "runtime token mismatch"
test -f /usr/lib/tmpfiles.d/crewquarters.conf || fail "tmpfiles"
[ "$(stat -c '%a %G' /run/crewquarters)" = "750 crewquarters" ] || fail "/run/crewquarters perms: $(stat -c '%a %U:%G' /run/crewquarters)"
echo "== socket dir: $(stat -c '%a %U:%G' /run/crewquarters)"
rm /etc/crewquarters/runtime-token
apt-get install -y -qq --reinstall "$DEB" >/dev/null 2>&1 || fail "reinstall with missing token"
test -s /etc/crewquarters/runtime-token || fail "token not regenerated"
echo "== missing runtime-token repaired"
T1=$(sha256sum /etc/crewquarters/secrets.env /etc/crewquarters/master.key)
apt-get install -y -qq --reinstall "$DEB" >/dev/null 2>&1
T2=$(sha256sum /etc/crewquarters/secrets.env /etc/crewquarters/master.key)
[ "$T1" = "$T2" ] || fail "reinstall changed secrets"
echo "== reinstall kept secrets: yes"
echo "== packaged daemon serving (python 3.10, host docker socket)"
CQ_RUNTIME_SOCKET=/tmp/rt.sock CQ_INTERNAL_SERVICE_TOKEN=deb-test-token-0000000000000000 CQ_RUNTIME_DATA_DIR=/tmp/cqdata \
  CQ_RUNTIME_MODEL_PROFILES=/usr/share/crewquarters/catalog/models \
  CQ_RUNTIME_AGENT_NETWORK=cq-agents-debtest CQ_RUNTIME_MODEL_NETWORK=cq-models-debtest \
  crewquarters-runtime serve > /tmp/daemon.log 2>&1 &
DAEMON=$!
for i in $(seq 1 30); do [ -S /tmp/rt.sock ] && break; sleep 0.5; done
curl -s --unix-socket /tmp/rt.sock -H "Authorization: Bearer deb-test-token-0000000000000000" http://runtime/internal/v1/host/capacity | python3 -c 'import sys,json;d=json.load(sys.stdin);print("capacity:",d["architecture"],"docker",d["docker"]["available"],"networks",d["networks"])'
curl -s --unix-socket /tmp/rt.sock -H "Authorization: Bearer deb-test-token-0000000000000000" http://runtime/internal/v1/models/local.general.small | python3 -c 'import sys,json;d=json.load(sys.stdin);print("dgx profile:",d["modelId"],d["files"]["state"],d["files"]["revision"][:7])'
NOTOKEN=$(curl -s -o /dev/null -w "%{http_code}" --unix-socket /tmp/rt.sock http://runtime/internal/v1/host/capacity)
[ "$NOTOKEN" = 401 ] || fail "daemon without a token -> $NOTOKEN"
echo "no token -> 401"
kill "$DAEMON"
echo "== preflight (container: expected failures)"; crewquarters-runtime preflight --json | python3 -c 'import sys,json;d=json.load(sys.stdin);print("passed=",d["passed"],[c["name"]+":"+c["status"] for c in d["checks"]])' || true

# --- Desktop launcher ------------------------------------------------------------------
echo "== desktop entry"
apt-get install -y -qq desktop-file-utils >/dev/null 2>&1
desktop-file-validate /usr/share/applications/crewquarters.desktop || fail "desktop entry does not validate"
grep -q '^Exec=/usr/share/crewquarters/launch.sh$' /usr/share/applications/crewquarters.desktop || fail "desktop Exec"
[ "$(stat -c '%a' /usr/share/crewquarters/launch.sh)" = 755 ] || fail "launcher not executable"
[ "$(cat /var/lib/crewquarters/public/ui-url)" = "http://localhost:8080" ] || fail "ui-url: $(cat /var/lib/crewquarters/public/ui-url)"
[ "$(stat -c '%a %U' /var/lib/crewquarters/public/ui-url)" = "644 root" ] || fail "ui-url perms"
# Stand-ins: a platform that answers /api/v1/health/ready, and xdg-open that records the URL.
cat > /tmp/platform.py <<'PY'
import http.server, ssl, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok"}' if self.path == "/api/v1/health/ready" else b'<div id="root"></div>'
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
if len(sys.argv) > 2:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(sys.argv[2], sys.argv[3])
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
PY
mkdir -p /tmp/stub && printf '#!/bin/sh\necho "$1" >> /tmp/opened\n' > /tmp/stub/xdg-open && chmod 0755 /tmp/stub/xdg-open
: > /tmp/opened && chmod 0666 /tmp/opened
launch() { # HOME-dir [env...]: run the launcher as an unprivileged desktop user
    home=$1; shift
    install -d -o nobody -g nogroup "$home"
    setpriv --reuid=nobody --regid=nogroup --clear-groups \
        env -i HOME="$home" PATH="/tmp/stub:/usr/bin:/bin" "$@" /usr/share/crewquarters/launch.sh
}
if launch /tmp/home-a CREWQUARTERS_WAIT_SECONDS=2 > /tmp/launch.out 2>&1; then fail "launcher succeeded with no platform"; fi
grep -q 'not running' /tmp/launch.out || fail "launcher message without platform: $(cat /tmp/launch.out)"
[ ! -s /tmp/opened ] || fail "opened a browser with no platform"
echo "no platform -> '$(tail -n 1 /tmp/launch.out)'"
# The platform comes up while the launcher waits.
launch /tmp/home-a CREWQUARTERS_WAIT_SECONDS=30 > /tmp/launch.out 2>&1 &
LAUNCH=$!
sleep 3
python3 /tmp/platform.py 8080 & PLATFORM=$!
wait "$LAUNCH" || fail "launcher did not open the UI once ready: $(cat /tmp/launch.out)"
grep -q 'starting' /tmp/launch.out || fail "no 'starting' message while waiting"
[ "$(tail -n 1 /tmp/opened)" = "http://localhost:8080/setup" ] || fail "first run opened $(tail -n 1 /tmp/opened)"
launch /tmp/home-a > /dev/null 2>&1 || fail "second launch"
[ "$(tail -n 1 /tmp/opened)" = "http://localhost:8080/" ] || fail "second run opened $(tail -n 1 /tmp/opened)"
kill "$PLATFORM"
echo "== launcher: waits for readiness; first run -> /setup, then -> /"

# --- LAN HTTPS mode --------------------------------------------------------------------
crewquarters lan-https status | grep -q 'LAN HTTPS mode: off' || fail "status before enable"
crewquarters lan-https enable --hostname spark --ip 192.168.50.10 > /tmp/lan.out 2>&1 || { cat /tmp/lan.out; fail "lan-https enable"; }
cat /tmp/lan.out
FP=$(openssl x509 -in /etc/crewquarters/tls/ca.crt -noout -fingerprint -sha256 | sed 's/^.*=//')
grep -q "CA fingerprint: *SHA-256 $FP" /tmp/lan.out || fail "enable did not print the CA fingerprint"
grep -q 'https://spark.local/setup' /tmp/lan.out || fail "enable did not print the URL"
grep -q 'stack is not running' /tmp/lan.out || fail "enable apply message"
[ "$(stat -c '%a %U:%G' /etc/crewquarters/tls)" = "750 root:crewquarters" ] || fail "tls dir perms"
[ "$(stat -c '%a %U:%G' /etc/crewquarters/tls/ca.key)" = "600 root:root" ] || fail "ca.key perms: $(stat -c '%a %U:%G' /etc/crewquarters/tls/ca.key)"
[ "$(stat -c '%a %U:%G' /etc/crewquarters/tls/server.key)" = "640 root:crewquarters" ] || fail "server.key perms"
[ "$(stat -c '%a' /etc/crewquarters/tls/ca.crt /etc/crewquarters/tls/server.crt | sort -u)" = 644 ] || fail "cert perms"
openssl verify -CAfile /etc/crewquarters/tls/ca.crt /etc/crewquarters/tls/server.crt >/dev/null || fail "certificate chain"
SAN=$(openssl x509 -in /etc/crewquarters/tls/server.crt -noout -ext subjectAltName | tail -n 1)
for want in DNS:spark DNS:spark.local DNS:localhost "IP Address:127.0.0.1" "IP Address:192.168.50.10"; do
    case "$SAN" in *"$want"*) ;; *) fail "SAN lacks $want: $SAN" ;; esac
done
openssl x509 -in /etc/crewquarters/tls/ca.crt -noout -ext nameConstraints | grep -q 'DNS:spark.local' || fail "CA not name-constrained"
ENV=/etc/crewquarters/lan-https.env
[ "$(stat -c '%a %U:%G' $ENV)" = "640 root:crewquarters" ] || fail "lan-https.env perms"
for want in 'CQ_LAN_HTTPS=true' 'CQ_BIND_ADDRESS=0.0.0.0' 'CQ_HTTP_PORT=80' 'CQ_PUBLIC_BASE_URL=https://spark.local' 'CQ_COOKIE_SECURE=true' \
        'CQ_PUBLIC_ORIGINS=["https://spark","https://spark.local","https://localhost","https://192.168.50.10"]'; do
    grep -qxF "$want" $ENV || fail "lan-https.env lacks $want: $(cat $ENV)"
done
grep -q '^CQ_PUBLIC_BASE_URL=http://localhost:8080$' /etc/crewquarters/crewquarters.env || fail "enable edited the conffile"
[ "$(cat /var/lib/crewquarters/public/ui-url)" = "https://spark.local" ] || fail "ui-url in LAN mode"
cmp -s /var/lib/crewquarters/public/ca.crt /etc/crewquarters/tls/ca.crt || fail "public CA copy"
[ "$(stat -c '%a' /var/lib/crewquarters/public/ca.crt)" = 644 ] || fail "public CA perms"
! ls /var/lib/crewquarters/public | grep -q key || fail "key material in the public dir"
crewquarters lan-https status | grep -q "CA fingerprint: SHA-256 $FP" || fail "status fingerprint"
echo "== LAN HTTPS enabled: CA 0600 root, server key 0640 root:crewquarters, env overrides, fingerprint printed"
# Re-issue (new address): the CA and its fingerprint stay, the certificate changes.
CA1=$(sha256sum /etc/crewquarters/tls/ca.key /etc/crewquarters/tls/ca.crt)
CERT1=$(sha256sum /etc/crewquarters/tls/server.crt)
crewquarters lan-https enable --hostname spark --ip 192.168.50.10 --ip 10.1.2.3 >/dev/null
[ "$CA1" = "$(sha256sum /etc/crewquarters/tls/ca.key /etc/crewquarters/tls/ca.crt)" ] || fail "re-issue replaced the CA"
[ "$CERT1" != "$(sha256sum /etc/crewquarters/tls/server.crt)" ] || fail "re-issue kept the certificate"
openssl x509 -in /etc/crewquarters/tls/server.crt -noout -ext subjectAltName | grep -q 'IP Address:10.1.2.3' || fail "new address missing"
grep -qF '"https://10.1.2.3"' $ENV || fail "new address not an allowed origin"
if crewquarters lan-https enable --hostname spark --ip 8.8.8.8 >/dev/null 2>&1; then fail "public address accepted"; fi
echo "== re-issue keeps the CA; public addresses refused"
# The launcher over HTTPS, verified against the device CA. First, this computer cannot
# resolve spark.local (no mDNS in the container): it opens https://localhost instead.
python3 /tmp/platform.py 443 /etc/crewquarters/tls/server.crt /etc/crewquarters/tls/server.key & TLSPLATFORM=$!
sleep 1
launch /tmp/home-b CREWQUARTERS_WAIT_SECONDS=10 > /tmp/launch.out 2>&1 || fail "launcher in LAN mode: $(cat /tmp/launch.out)"
[ "$(tail -n 1 /tmp/opened)" = "https://localhost/setup" ] || fail "LAN first run (no mDNS) opened $(tail -n 1 /tmp/opened)"
echo "127.0.0.1 spark.local" >> /etc/hosts
launch /tmp/home-b > /dev/null 2>&1 || fail "LAN second launch"
[ "$(tail -n 1 /tmp/opened)" = "https://spark.local/" ] || fail "LAN second run opened $(tail -n 1 /tmp/opened)"
# A certificate the device CA did not issue is refused (the launcher never opens it).
kill "$TLSPLATFORM"; wait "$TLSPLATFORM" 2>/dev/null || true
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout /tmp/other.key -out /tmp/other.crt \
    -days 1 -subj /CN=spark.local -addext subjectAltName=DNS:spark.local 2>/dev/null
python3 /tmp/platform.py 443 /tmp/other.crt /tmp/other.key & TLSPLATFORM=$!
sleep 1
if launch /tmp/home-b CREWQUARTERS_WAIT_SECONDS=2 > /tmp/launch.out 2>&1; then fail "launcher trusted a foreign certificate"; fi
kill "$TLSPLATFORM"
grep -v ' spark.local$' /etc/hosts > /tmp/hosts; cat /tmp/hosts > /etc/hosts  # bind-mounted: no rename
echo "== launcher in LAN HTTPS mode: CA-verified readiness; /setup then /; https://localhost without mDNS"
crewquarters lan-https disable > /tmp/lan.out 2>&1 || { cat /tmp/lan.out; fail "disable"; }
[ ! -e $ENV ] || fail "disable kept lan-https.env"
[ -f /etc/crewquarters/tls/ca.key ] || fail "disable removed the CA"
[ "$(cat /var/lib/crewquarters/public/ui-url)" = "http://localhost:8080" ] || fail "ui-url after disable"
[ ! -e /var/lib/crewquarters/public/ca.crt ] || fail "public CA left after disable"
crewquarters lan-https status | grep -q 'LAN HTTPS mode: off' || fail "status after disable"
crewquarters lan-https disable --forget-ca >/dev/null
[ ! -e /etc/crewquarters/tls ] || fail "--forget-ca kept the CA"
echo "== LAN HTTPS disabled (CA kept); --forget-ca deletes it"

touch /var/lib/crewquarters/models/keep
apt-get remove -y -qq crewquarters >/dev/null 2>&1
[ -f /var/lib/crewquarters/models/keep ] && [ -f /etc/crewquarters/secrets.env ] && [ ! -e /usr/bin/crewquarters ] || fail "remove"
echo "== after remove: data kept, secrets kept, binaries gone"
PW_BEFORE=$(grep '^POSTGRES_PASSWORD=' /etc/crewquarters/secrets.env)
apt-get purge -y -qq crewquarters >/dev/null 2>&1
[ -f /var/lib/crewquarters/.retained-config/secrets.env ] || fail "secrets not retained"
[ -f /var/lib/crewquarters/models/keep ] && [ ! -e /etc/crewquarters ] && ! getent passwd crewquarters-runtime >/dev/null || fail "purge"
echo "== after purge: data kept, /etc/crewquarters gone, users gone"
# Headless install: LAN HTTPS mode on from the start.
CREWQUARTERS_HEADLESS=yes CREWQUARTERS_LAN_HOSTNAME=spark apt-get install -y -qq "$DEB" > /tmp/install.log 2>&1 || { cat /tmp/install.log; fail "headless install"; }
[ "$(grep '^POSTGRES_PASSWORD=' /etc/crewquarters/secrets.env)" = "$PW_BEFORE" ] || fail "secrets not restored"
echo "== reinstall after purge restored the retained secrets"
grep -qx 'CQ_PUBLIC_BASE_URL=https://spark.local' /etc/crewquarters/lan-https.env || fail "headless install did not enable LAN HTTPS"
grep -q 'CA fingerprint: *SHA-256 [0-9A-F:]\{95\}' /tmp/install.log || fail "headless install did not print the fingerprint"
grep -q 'Open the setup UI: *https://spark.local/setup' /tmp/install.log || fail "headless install URL"
[ "$(stat -c '%a %U:%G' /etc/crewquarters/tls/ca.key)" = "600 root:root" ] || fail "headless ca.key perms"
apt-get install -y -qq --reinstall "$DEB" >/dev/null 2>&1
[ -f /etc/crewquarters/lan-https.env ] || fail "upgrade dropped LAN HTTPS mode"
echo "== headless install: LAN HTTPS on, URL and fingerprint printed; kept on reinstall"
apt-get purge -y -qq crewquarters >/dev/null 2>&1
[ ! -e /etc/crewquarters/tls ] && [ ! -e /etc/crewquarters/lan-https.env ] || fail "purge kept the device CA"
apt-get install -y -qq "$DEB" >/dev/null 2>&1
CREWQUARTERS_PURGE_DATA=yes apt-get purge -y -qq crewquarters >/dev/null 2>&1
[ ! -e /var/lib/crewquarters ] || fail "explicit data purge"
echo "== explicit data purge: data gone"
echo "ALL DEB CHECKS PASSED"
