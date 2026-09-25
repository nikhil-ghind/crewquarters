#!/bin/bash
# Proxy-level test of both modes against stub backends (no platform stack needed):
#   docker build -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:test .
#   infra/proxy/test-proxy.sh crewquarters/proxy:test
# LAN HTTPS mode: a device CA and certificate from infra/debian/bin/lan-tls.sh, the TLS
# handshake verified with curl against that CA (and only that CA), TLS 1.2/1.3 only, HSTS
# over HTTPS names only, SPA/API/callback routing over HTTPS, HTTP -> HTTPS redirect, the
# CA download, and the healthcheck. Default mode: HTTP routing and no HSTS/TLS.
# Needs docker, curl and openssl. Exits non-zero on the first failed expectation.
set -euo pipefail
IMAGE="${1:-crewquarters/proxy:test}"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ID="cqproxytest-$$"
NET="$ID-net"
TMP=$(mktemp -d)
cleanup() {
    docker rm -f "$ID-api" "$ID-broker" "$ID-lan" "$ID-http" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    rm -rf "$TMP"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok   $*"; }

# --- stub backends: echo who answered and the forwarded scheme/port ----------------------
stub() { # name port
    cat > "$TMP/$1.conf" <<CONF
pid /tmp/nginx.pid;
events {}
http {
    access_log off;
    client_body_temp_path /tmp/cb;
    server {
        listen $2;
        location / {
            default_type application/json;
            return 200 '{"service":"$1","path":"\$uri","proto":"\$http_x_forwarded_proto","port":"\$http_x_forwarded_port"}\n';
        }
    }
}
CONF
    chmod 0644 "$TMP/$1.conf"
}
stub control-api 8080
stub capability-broker 8000
docker network create "$NET" >/dev/null
docker run -d --name "$ID-api" --network "$NET" --network-alias control-api \
    -v "$TMP/control-api.conf:/stub.conf:ro" "$IMAGE" nginx -c /stub.conf -g 'daemon off;' >/dev/null
docker run -d --name "$ID-broker" --network "$NET" --network-alias capability-broker \
    -v "$TMP/capability-broker.conf:/stub.conf:ro" "$IMAGE" nginx -c /stub.conf -g 'daemon off;' >/dev/null

# --- device certificate, as `crewquarters lan-https enable` makes it ----------------------
TLS="$TMP/tls"
"$ROOT/infra/debian/bin/lan-tls.sh" issue "$TLS" spark 192.168.50.10
# As on the appliance: key readable by a group the proxy is added to, never by others.
chgrp "$(id -g)" "$TLS/server.key" && chmod 0640 "$TLS/server.key"
chmod 0755 "$TLS"
FP=$("$ROOT/infra/debian/bin/lan-tls.sh" fingerprint "$TLS")
[[ "$FP" =~ ^([0-9A-F]{2}:){31}[0-9A-F]{2}$ ]] || fail "fingerprint format: $FP"
pass "device CA fingerprint $FP"
# Name constraints: the CA cannot vouch for any other site.
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$TMP/evil.key" 2>/dev/null
openssl req -new -key "$TMP/evil.key" -subj "/CN=bank.example.com" -out "$TMP/evil.csr"
printf 'subjectAltName=DNS:bank.example.com\n' > "$TMP/evil.ext"
openssl x509 -req -in "$TMP/evil.csr" -CA "$TLS/ca.crt" -CAkey "$TLS/ca.key" -set_serial 7 \
    -days 1 -extfile "$TMP/evil.ext" -out "$TMP/evil.crt" 2>/dev/null
if openssl verify -CAfile "$TLS/ca.crt" "$TMP/evil.crt" >/dev/null 2>&1; then
    fail "the device CA can sign for other names"
fi
pass "device CA is name-constrained (a certificate for bank.example.com does not verify)"

# --- LAN HTTPS mode ----------------------------------------------------------------------
docker run -d --name "$ID-lan" --network "$NET" --read-only --tmpfs /tmp --cap-drop ALL \
    --security-opt no-new-privileges:true --group-add "$(id -g)" \
    -v "$TLS/server.crt:/run/crewquarters-tls/server.crt:ro" \
    -v "$TLS/server.key:/run/crewquarters-tls/server.key:ro" \
    -v "$TLS/ca.crt:/run/crewquarters-tls/ca.crt:ro" \
    -p 127.0.0.1::8443 -p 127.0.0.1::8080 \
    "$IMAGE" nginx -c /etc/nginx/nginx-lan-https.conf -g 'daemon off;' >/dev/null
HTTPS_PORT=$(docker port "$ID-lan" 8443/tcp | head -n1 | sed 's/.*://')
HTTP_PORT=$(docker port "$ID-lan" 8080/tcp | head -n1 | sed 's/.*://')
for _ in $(seq 1 50); do
    curl -s -o /dev/null --cacert "$TLS/ca.crt" --resolve "spark.local:$HTTPS_PORT:127.0.0.1" \
        "https://spark.local:$HTTPS_PORT/index.html" && break
    sleep 0.2
done
S="https://spark.local:$HTTPS_PORT"
C=(curl -sS --cacert "$TLS/ca.crt" --resolve "spark.local:$HTTPS_PORT:127.0.0.1")

headers=$("${C[@]}" -D - -o "$TMP/setup.html" "$S/setup")
grep -q '^HTTP/2 200' <<<"$headers" || fail "GET /setup over HTTPS: $headers"
grep -qi '^content-type: text/html' <<<"$headers" || fail "SPA content type"
grep -q '<div id="root">' "$TMP/setup.html" || fail "SPA fallback did not serve index.html"
grep -qi "^content-security-policy: default-src 'self'" <<<"$headers" || fail "CSP missing"
grep -qi '^strict-transport-security: max-age=31536000' <<<"$headers" || fail "HSTS missing over HTTPS"
[ "$(grep -ci '^strict-transport-security' <<<"$headers")" = 1 ] || fail "HSTS sent twice"
pass "TLS handshake verified against the device CA; /setup serves the SPA with CSP and HSTS (HTTP/2)"

# Every SAN: the .local name, the bare host name and the LAN address.
for target in "spark:443" "192.168.50.10:443"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$TLS/ca.crt" \
        --connect-to "$target:127.0.0.1:$HTTPS_PORT" "https://${target%:443}/")
    [ "$code" = 200 ] || fail "https://${target%:443}/ -> $code"
done
pass "certificate also valid for https://spark and https://192.168.50.10"

if curl -s -o /dev/null --resolve "spark.local:$HTTPS_PORT:127.0.0.1" "$S/"; then
    fail "the device certificate verified without the device CA"
fi
pass "without the device CA the handshake is rejected (not a public certificate)"

no_hsts=$(curl -sS -D - -o /dev/null --cacert "$TLS/ca.crt" "https://localhost:$HTTPS_PORT/")
grep -q '^HTTP/2 200' <<<"$no_hsts" || fail "https://localhost"
if grep -qi '^strict-transport-security' <<<"$no_hsts"; then fail "HSTS sent for localhost"; fi
pass "https://localhost works and gets no HSTS (so http://localhost keeps working after disable)"

"${C[@]}" -o /dev/null --tlsv1.2 --tls-max 1.2 "$S/" || fail "TLS 1.2 refused"
"${C[@]}" -o /dev/null --tlsv1.3 "$S/" || fail "TLS 1.3 refused"
if "${C[@]}" -o /dev/null --tlsv1.1 --tls-max 1.1 "$S/" 2>/dev/null; then fail "TLS 1.1 accepted"; fi
if "${C[@]}" -o /dev/null --tlsv1.2 --tls-max 1.2 --ciphers AES128-SHA "$S/" 2>/dev/null; then
    fail "a non-forward-secret CBC cipher was accepted"
fi
pass "TLS 1.2 and 1.3 only; no static-RSA/CBC ciphers"

api=$("${C[@]}" "$S/api/v1/health/ready")
[[ "$api" == *'"service":"control-api"'* && "$api" == *'"proto":"https"'* && "$api" == *'"port":"443"'* ]] \
    || fail "API routing over HTTPS: $api"
cb=$("${C[@]}" "$S/api/v1/connections/google/callback?state=x")
[[ "$cb" == *'"service":"capability-broker"'* ]] || fail "Google callback routing: $cb"
tw=$("${C[@]}" -X POST "$S/api/v1/callbacks/twilio/status/1")
[[ "$tw" == *'"service":"capability-broker"'* ]] || fail "Twilio callback routing: $tw"
code=$("${C[@]}" -o "$TMP/internal.json" -w '%{http_code}' "$S/internal/v1/metrics")
[ "$code" = 404 ] && grep -q '"NOT_FOUND"' "$TMP/internal.json" || fail "/internal over HTTPS -> $code"
pass "over HTTPS: /api -> control API (X-Forwarded-Proto https, port 443), callbacks -> broker, /internal -> 404"

font=$(docker exec "$ID-lan" sh -c 'ls /usr/share/crewquarters/ui/assets | grep -m1 "^InterVariable-.*\.woff2$"') \
    || fail "no bundled Inter font in the image"
fh=$("${C[@]}" -D - -o /dev/null "$S/assets/$font")
grep -q '^HTTP/2 200' <<<"$fh" && grep -qi '^content-type: font/woff2' <<<"$fh" \
    && grep -qi 'immutable' <<<"$fh" || fail "font asset: $fh"
css=$(docker exec "$ID-lan" sh -c 'cat /usr/share/crewquarters/ui/assets/index-*.css')
[[ "$css" == *"/assets/$font"* ]] || fail "the stylesheet does not reference the bundled font"
pass "bundled Inter font served from the device (/assets/$font, font/woff2), referenced by the CSS"

# HTTP listener: redirect to the same host over HTTPS; the CA stays downloadable.
redir=$(curl -sS -D - -o /dev/null -H "Host: spark.local" "http://127.0.0.1:$HTTP_PORT/runs/7?tab=log")
grep -q '^HTTP/1.1 301' <<<"$redir" || fail "HTTP redirect: $redir"
grep -qi '^location: https://spark.local/runs/7?tab=log' <<<"$redir" || fail "redirect target: $redir"
redir_api=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' -H "Host: 192.168.50.10" \
    "http://127.0.0.1:$HTTP_PORT/api/v1/health/ready")
[ "$redir_api" = "301 https://192.168.50.10/api/v1/health/ready" ] || fail "API redirect: $redir_api"
ca=$(curl -sS -D - -o "$TMP/ca-download.crt" "http://127.0.0.1:$HTTP_PORT/crewquarters-ca.crt")
grep -qi '^content-type: application/x-x509-ca-cert' <<<"$ca" || fail "CA download type: $ca"
cmp -s "$TMP/ca-download.crt" "$TLS/ca.crt" || fail "CA download differs from the CA"
if grep -q 'PRIVATE KEY' "$TMP/ca-download.crt"; then fail "key material in the CA download"; fi
pass "HTTP -> 301 https://<same host><same path>; /crewquarters-ca.crt downloadable over HTTP"

docker exec "$ID-lan" wget -q --no-check-certificate -O /dev/null https://127.0.0.1:8443/index.html || fail "LAN healthcheck command"
code=$(docker exec "$ID-lan" sh -c 'wget -q -S -O /dev/null http://127.0.0.1:8081/ 2>&1 | head -n1')
[[ "$code" == *404* ]] || fail "callbacks site in LAN mode: $code"
pass "healthcheck (wget https://127.0.0.1:8443, as in compose.lan-https.yaml) passes; callbacks site still 404s the UI"

# --- default mode ------------------------------------------------------------------------
docker run -d --name "$ID-http" --network "$NET" --read-only --tmpfs /tmp --cap-drop ALL \
    -p 127.0.0.1::8080 "$IMAGE" >/dev/null
PORT=$(docker port "$ID-http" 8080/tcp | head -n1 | sed 's/.*://')
for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$PORT/index.html" && break; sleep 0.2; done
h=$(curl -sS -D - -o /dev/null "http://127.0.0.1:$PORT/setup")
grep -q '^HTTP/1.1 200' <<<"$h" || fail "default mode /setup: $h"
if grep -qi '^strict-transport-security' <<<"$h"; then fail "HSTS in default mode"; fi
api=$(curl -sS "http://127.0.0.1:$PORT/api/v1/health/ready")
[[ "$api" == *'"service":"control-api"'* && "$api" == *'"proto":"http"'* ]] || fail "default API: $api"
body=$(curl -sS "http://127.0.0.1:$PORT/crewquarters-ca.crt")
[[ "$body" == *'<div id="root">'* ]] || fail "default mode serves a CA download"
docker exec "$ID-http" wget -q -O /dev/null http://127.0.0.1:8080/index.html || fail "default healthcheck"
if docker exec "$ID-http" wget -q -O /dev/null https://127.0.0.1:8443/ 2>/dev/null; then
    fail "default mode listens on 8443"
fi
pass "default mode: HTTP only, no HSTS, no TLS listener, same routing"
echo "ALL PROXY CHECKS PASSED"
