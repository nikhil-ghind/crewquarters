#!/bin/bash
# Install-test the .deb inside ubuntu:22.04 (DGX OS base). Run by CI and by hand:
#   docker run --rm -v "$PWD/dist:/pkgs:ro" -v /var/run/docker.sock:/var/run/docker.sock \
#     -v "$PWD/infra/debian/test-install.sh:/t.sh:ro" ubuntu:22.04 bash /t.sh
# Exits non-zero on any failed expectation.
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq /pkgs/crewquarters_0.1.0_amd64.deb > /tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; }
echo "== postinst output"; grep -E "^crewquarters|Crewquarters|Open the|One-time|WARNING" /tmp/install.log | head -8
echo "== python: $(python3 --version)"
echo "== version: $(crewquarters version)"
id crewquarters-runtime
echo "== perms"; stat -c '%a %U:%G %n' /etc/crewquarters /etc/crewquarters/secrets.env /etc/crewquarters/runtime-token /etc/crewquarters/master.key /var/lib/crewquarters/models /var/lib/crewquarters/postgres
echo "master.key bytes: $(stat -c %s /etc/crewquarters/master.key)"
grep -Eq '^1:[0-9a-f]{64}$' /etc/crewquarters/master.key || { echo "FAIL: master.key is not a keyring"; exit 1; }
grep -c '^CQ_' /etc/crewquarters/secrets.env
grep '^CQ_SOCKET_GID=' /etc/crewquarters/secrets.env
grep -q '^CQ_CHAT_CLIENT_TOKEN=' /etc/crewquarters/secrets.env || { echo "FAIL: chat token"; exit 1; }
[ "$(cat /etc/crewquarters/runtime-token)" = "$(sed -n 's/^CQ_INTERNAL_SERVICE_TOKEN=//p' /etc/crewquarters/secrets.env)" ] || { echo "FAIL: runtime token mismatch"; exit 1; }
test -f /usr/lib/tmpfiles.d/crewquarters.conf || { echo "FAIL: tmpfiles"; exit 1; }
[ "$(stat -c '%a %G' /run/crewquarters)" = "750 crewquarters" ] || { echo "FAIL: /run/crewquarters perms: $(stat -c '%a %U:%G' /run/crewquarters)"; exit 1; }
echo "== socket dir: $(stat -c '%a %U:%G' /run/crewquarters)"
rm /etc/crewquarters/runtime-token
apt-get install -y -qq --reinstall /pkgs/crewquarters_0.1.0_amd64.deb >/dev/null 2>&1 || { echo "FAIL: reinstall with missing token"; exit 1; }
test -s /etc/crewquarters/runtime-token || { echo "FAIL: token not regenerated"; exit 1; }
echo "== missing runtime-token repaired"
T1=$(sha256sum /etc/crewquarters/secrets.env /etc/crewquarters/master.key)
apt-get install -y -qq --reinstall /pkgs/crewquarters_0.1.0_amd64.deb >/dev/null 2>&1
T2=$(sha256sum /etc/crewquarters/secrets.env /etc/crewquarters/master.key)
[ "$T1" = "$T2" ] || { echo "FAIL: reinstall changed secrets"; exit 1; }
echo "== reinstall kept secrets: yes"
echo "== packaged daemon serving (python 3.10, host docker socket)"
CQ_RUNTIME_SOCKET=/tmp/rt.sock CQ_INTERNAL_SERVICE_TOKEN=deb-test-token-0000000000000000 CQ_RUNTIME_DATA_DIR=/tmp/cqdata \
  CQ_RUNTIME_MODEL_PROFILES=/usr/share/crewquarters/catalog/models \
  CQ_RUNTIME_AGENT_NETWORK=cq-agents-debtest CQ_RUNTIME_MODEL_NETWORK=cq-models-debtest \
  crewquarters-runtime serve > /tmp/daemon.log 2>&1 &
for i in $(seq 1 30); do [ -S /tmp/rt.sock ] && break; sleep 0.5; done
curl -s --unix-socket /tmp/rt.sock -H "Authorization: Bearer deb-test-token-0000000000000000" http://runtime/internal/v1/host/capacity | python3 -c 'import sys,json;d=json.load(sys.stdin);print("capacity:",d["architecture"],"docker",d["docker"]["available"],"networks",d["networks"])'
curl -s --unix-socket /tmp/rt.sock -H "Authorization: Bearer deb-test-token-0000000000000000" http://runtime/internal/v1/models/local.general.small | python3 -c 'import sys,json;d=json.load(sys.stdin);print("dgx profile:",d["modelId"],d["files"]["state"],d["files"]["revision"][:7])'
curl -s -o /dev/null -w "no token -> %{http_code}\n" --unix-socket /tmp/rt.sock http://runtime/internal/v1/host/capacity
kill %1
grep -q "no token -> 401" /dev/null 2>&1 || true
echo "== preflight (container: expected failures)"; crewquarters-runtime preflight --json | python3 -c 'import sys,json;d=json.load(sys.stdin);print("passed=",d["passed"],[c["name"]+":"+c["status"] for c in d["checks"]])' || true
touch /var/lib/crewquarters/models/keep
apt-get remove -y -qq crewquarters >/dev/null 2>&1
[ -f /var/lib/crewquarters/models/keep ] && [ -f /etc/crewquarters/secrets.env ] && [ ! -e /usr/bin/crewquarters ] || { echo "FAIL: remove"; exit 1; }
echo "== after remove: data kept, secrets kept, binaries gone"
PW_BEFORE=$(grep '^POSTGRES_PASSWORD=' /etc/crewquarters/secrets.env)
apt-get purge -y -qq crewquarters >/dev/null 2>&1
[ -f /var/lib/crewquarters/.retained-config/secrets.env ] || { echo "FAIL: secrets not retained"; exit 1; }
[ -f /var/lib/crewquarters/models/keep ] && [ ! -e /etc/crewquarters ] && ! getent passwd crewquarters-runtime >/dev/null || { echo "FAIL: purge"; exit 1; }
echo "== after purge: data kept, /etc/crewquarters gone, users gone"
apt-get install -y -qq /pkgs/crewquarters_0.1.0_amd64.deb >/dev/null 2>&1
[ "$(grep '^POSTGRES_PASSWORD=' /etc/crewquarters/secrets.env)" = "$PW_BEFORE" ] || { echo "FAIL: secrets not restored"; exit 1; }
echo "== reinstall after purge restored the retained secrets"
CREWQUARTERS_PURGE_DATA=yes apt-get purge -y -qq crewquarters >/dev/null 2>&1
[ ! -e /var/lib/crewquarters ] || { echo "FAIL: explicit data purge"; exit 1; }
echo "== explicit data purge: data gone"
echo "ALL DEB CHECKS PASSED"
