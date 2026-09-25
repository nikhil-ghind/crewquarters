"""contract_probe from its registry image, through the whole platform (PLAN.md §23.6).

The probe reports its own checks; the tests below also look at the agent from inside its
container (``docker exec`` as the agent's user, in the agent's network namespace) to prove
the capability matrix and the network isolation directly against the real broker.
"""

from __future__ import annotations

import json
from typing import Any

PROBE = "contract-probe"
ALL_CHECKS = [
    "handshake",
    "events",
    "input",
    "llm",
    "structured",
    "knowledge",
    "idempotency",
    "permissions",
    "isolation",
]

# Runs inside the agent container: the SDK's own environment, stdlib only.
CAPABILITY_MATRIX = r"""
import json, os, sys, urllib.request, urllib.error
base = os.environ["PLATFORM_BROKER_URL"].rstrip("/") + "/internal/v1/sdk"
token = os.environ["PLATFORM_RUN_TOKEN"]
kb = sys.argv[1]

def call(method, path, body=None, auth=token):
    req = urllib.request.Request(base + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return [resp.status, None]
    except urllib.error.HTTPError as exc:
        try:
            code = json.loads(exc.read())["error"]["code"]
        except Exception:
            code = None
        return [exc.code, code]

msg = [{"role": "user", "content": "ping"}]
call_body = {"to": "+15555550101", "script": {"disclosure": "d", "text": "t"},
             "gather": {"input": "speech", "timeoutSeconds": 5}, "idempotencyKey": "matrix"}
print(json.dumps({
  "llm.local.general": call("POST", "/llm/chat", {"profile": "local.general", "messages": msg}),
  "llm.openai": call("POST", "/llm/chat", {"profile": "openai.general", "messages": msg}),
  "llm.other.variant": call("POST", "/llm/chat",
                           {"profile": "local.general.quality", "messages": msg}),
  "knowledge.approved": call("POST", "/knowledge/search", {"knowledgeBaseId": kb, "query": "probe"}),
  "knowledge.other": call("POST", "/knowledge/search",
                          {"knowledgeBaseId": "00000000-0000-0000-0000-000000000000", "query": "x"}),
  "gmail.list": call("GET", "/google/gmail/messages"),
  "gmail.get": call("GET", "/google/gmail/messages/m-plain"),
  "sheets.get": call("POST", "/google/sheets/values:get", {"spreadsheetId": "s", "range": "A!A1"}),
  "sheets.update": call("POST", "/google/sheets/values:update",
                        {"spreadsheetId": "s", "range": "A!A1", "values": [["x"]]}),
  "telephony.create": call("POST", "/telephony/calls", call_body),
  "heartbeat": call("POST", "/heartbeat", {}),
  "forged.token": call("GET", "/google/gmail/messages", auth=token[:-4] + "AAAA"),
}))
"""

ISOLATION = r"""
import json, os, socket, sys
targets = json.loads(sys.argv[1])
def reach(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True
    except OSError:
        return False
def resolves(name):
    try:
        socket.getaddrinfo(name, 443)
        return True
    except OSError:
        return False
result = {label: reach(host, port) for label, host, port in targets}
result["dns:example.com"] = resolves("example.com")
result["dns:postgres"] = resolves("postgres")
result["docker.sock"] = any(os.path.exists(p) for p in ("/var/run/docker.sock", "/run/docker.sock"))
result["uid"] = os.getuid()
print(json.dumps(result))
"""


def test_contract_probe_passes_every_check(
    owner: Any, stack: Any, knowledge_base: dict[str, Any], healthy: None
) -> None:
    installation = owner.install(PROBE, {"knowledgeBaseId": knowledge_base["id"]})
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])
    assert request["key"] == "probe-input-v1"
    assert owner.answer(request, {"choice": "ok"}).status_code == 200
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=180)

    checks = {c["name"]: c for c in done["result"]["checks"]}
    assert {n: c["status"] for n, c in checks.items()} == dict.fromkeys(ALL_CHECKS, "passed"), (
        checks
    )
    assert checks["handshake"]["detail"].endswith("trigger manual")
    assert "answered locally" in checks["llm"]["detail"]
    assert checks["isolation"]["detail"].startswith("uid 65532, read-only root")
    # The knowledge service answered with a cited passage of the document uploaded above.
    assert "passages, top citation" in checks["knowledge"]["detail"]
    # LOADING_MODEL appears only when the model was cold.
    states = [s for s in owner.states(run["id"]) if s != "LOADING_MODEL"]
    assert states == ["QUEUED", "PREPARING", "RUNNING", "WAITING_INPUT", "RUNNING"] + [
        "RUNNING"
    ] * (len(states) - 6) + ["SUCCEEDED"]


def test_capability_matrix_and_isolation_from_inside_the_agent_container(
    owner: Any, stack: Any, fakes: Any, knowledge_base: dict[str, Any], google: Any
) -> None:
    """Every broker capability, allowed or denied, with the agent's real token; and no
    route from the agent's network to anything but the broker."""
    installation = owner.install(
        PROBE, {"knowledgeBaseId": knowledge_base["id"], "checks": ["handshake", "input"]}
    )
    run = owner.start(installation["id"])
    request = owner.pending_input(run["id"])  # the agent is alive and waiting
    container = stack.run_container(run["id"])
    info = stack.inspect(container)
    assert info is not None
    host = info["HostConfig"]
    assert host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"]
    assert host["Privileged"] is False and "no-new-privileges:true" in host["SecurityOpt"]
    assert host["NetworkMode"] == "cqreal-agents" and host["Memory"] == 256 * 1024 * 1024
    assert info["Config"]["User"] == "65532:65532"
    assert info["Config"]["Image"].split("@")[1].startswith("sha256:")
    assert not any(b.endswith("docker.sock") for b in host.get("Binds") or [])

    calls_before = len(fakes.state()["calls"])
    matrix = stack.exec_python(container, CAPABILITY_MATRIX, knowledge_base["id"])
    assert matrix == {
        # granted by the manifest and the owner's approval
        "llm.local.general": [200, None],
        "knowledge.approved": [200, None],
        "heartbeat": [200, None],
        # not granted, or not the approved resource
        "llm.openai": [403, "CAPABILITY_DENIED"],
        "llm.other.variant": [403, "CAPABILITY_DENIED"],
        "knowledge.other": [403, "PERMISSION_DENIED"],
        "gmail.list": [403, "CAPABILITY_DENIED"],
        "gmail.get": [403, "CAPABILITY_DENIED"],
        "sheets.get": [403, "CAPABILITY_DENIED"],
        "sheets.update": [403, "CAPABILITY_DENIED"],
        "telephony.create": [403, "CAPABILITY_DENIED"],
        "forged.token": [401, "UNAUTHENTICATED"],
    }, matrix
    assert len(fakes.state()["calls"]) == calls_before, "a denied call must not reach Twilio"

    targets = [
        ["broker", "capability-broker", 8000],  # the one allowed peer
        ["postgres", stack.service_ip("postgres"), 5432],
        ["control-api", stack.service_ip("control-api"), 8080],
        ["model-gateway", stack.service_ip("model-gateway"), 8090],
        ["knowledge", stack.service_ip("knowledge"), 8000],
        ["proxy", stack.service_ip("proxy"), 8080],
        ["internet", "1.1.1.1", 443],
        ["host-docker0", "172.17.0.1", 18083],
    ]
    reach = stack.exec_python(container, ISOLATION, json.dumps(targets))
    assert reach == {
        "broker": True,
        "postgres": False,
        "control-api": False,
        "model-gateway": False,
        "knowledge": False,
        "proxy": False,
        "internet": False,
        "host-docker0": False,
        "dns:example.com": False,
        "dns:postgres": False,
        "docker.sock": False,
        "uid": 65532,
    }, reach

    # The test harness's admin surface on the broker is closed to the agent's token-less calls.
    admin = stack.exec_python(
        container,
        "import json,urllib.request,urllib.error\n"
        "try:\n"
        " urllib.request.urlopen('http://capability-broker:8000/__realstack/state',timeout=5)\n"
        " print(json.dumps({'status':200}))\n"
        "except urllib.error.HTTPError as e:\n"
        " print(json.dumps({'status':e.code}))",
    )
    assert admin == {"status": 404}

    assert owner.answer(request, {"choice": "ok"}).status_code == 200
    owner.wait_state(run["id"], "SUCCEEDED")
