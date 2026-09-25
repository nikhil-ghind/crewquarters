"""HTTP/1.1 server on a Unix socket (never TCP) implementing the internal runtime API.

Routes (PLAN.md section 5.2), all requiring ``Authorization: Bearer <service token>``:

    GET  /internal/v1/health
    GET  /internal/v1/host/capacity
    POST /internal/v1/images/pull                    {"image": "name@sha256:..."}
    POST /internal/v1/runs                           RunSpec -> {"runtimeRef"}
    GET  /internal/v1/runs/{ref}                     {state, exitCode, ...}
    GET  /internal/v1/runs/{ref}/logs?tail=N         bounded logs
    POST /internal/v1/runs/{ref}/cancel              {"graceSeconds"}
    GET  /internal/v1/models/{id}                    files + container state
    POST /internal/v1/models/{id}/install            start/resume download
    POST /internal/v1/models/{id}/install/cancel     {"clear": bool}
    DELETE /internal/v1/models/{id}/files            delete installed files
    POST /internal/v1/models/{id}/prepare            pull the serving image (slow, no lock)
    POST /internal/v1/models/{id}/start              start allowlisted server
    POST /internal/v1/models/{id}/stop               stop + verify release
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import socket
import socketserver
import stat
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from crewquarters_runtime.docker_api import DockerError
from crewquarters_runtime.engine import Engine
from crewquarters_runtime.specs import SpecError

log = logging.getLogger("crewquarters.runtime.http")

MAX_BODY = 1024 * 1024
MIN_TOKEN_LENGTH = 16
Handler = Callable[[Engine, dict[str, Any], dict[str, list[str]], list[str]], tuple[int, Any]]


def _json_body(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SpecError("INVALID_BODY", "Request body must be a JSON object.")
    return data


ROUTES: list[tuple[str, re.Pattern[str], Handler]] = [
    (
        "POST",
        re.compile(r"^/internal/v1/models/([^/]+)/prepare$"),
        lambda e, b, q, m: (200, e.prepare_model(m[0])),
    ),
    ("GET", re.compile(r"^/internal/v1/health$"), lambda e, b, q, m: (200, {"status": "ok"})),
    ("GET", re.compile(r"^/internal/v1/host/capacity$"), lambda e, b, q, m: (200, e.capacity())),
    (
        "POST",
        re.compile(r"^/internal/v1/images/pull$"),
        lambda e, b, q, m: (200, e.pull(str(b.get("image", "")))),
    ),
    ("POST", re.compile(r"^/internal/v1/runs$"), lambda e, b, q, m: (201, e.start_run(b))),
    (
        "GET",
        re.compile(r"^/internal/v1/runs/([^/]+)$"),
        lambda e, b, q, m: _found(e.run_status(m[0])),
    ),
    (
        "GET",
        re.compile(r"^/internal/v1/runs/([^/]+)/logs$"),
        lambda e, b, q, m: (200, e.run_logs(m[0], int(q.get("tail", ["200"])[0]))),
    ),
    (
        "POST",
        re.compile(r"^/internal/v1/runs/([^/]+)/cancel$"),
        lambda e, b, q, m: (200, e.cancel_run(m[0], int(b.get("graceSeconds", 10)))),
    ),
    (
        "GET",
        re.compile(r"^/internal/v1/models/([^/]+)$"),
        lambda e, b, q, m: (200, e.model_state(m[0])),
    ),
    (
        "POST",
        re.compile(r"^/internal/v1/models/([^/]+)/install$"),
        lambda e, b, q, m: (202, e.install_model(m[0])),
    ),
    (
        "POST",
        re.compile(r"^/internal/v1/models/([^/]+)/install/cancel$"),
        lambda e, b, q, m: (200, e.cancel_install(m[0], bool(b.get("clear", False)))),
    ),
    (
        "DELETE",
        re.compile(r"^/internal/v1/models/([^/]+)/files$"),
        lambda e, b, q, m: (200, e.delete_model(m[0])),
    ),
    (
        "POST",
        re.compile(r"^/internal/v1/models/([^/]+)/start$"),
        lambda e, b, q, m: (200, e.start_model(m[0])),
    ),
    (
        "POST",
        re.compile(r"^/internal/v1/models/([^/]+)/stop$"),
        lambda e, b, q, m: (200, e.stop_model(m[0], int(b.get("graceSeconds", 30)))),
    ),
]

STATUS_FOR_CODE = {
    "NOT_FOUND": 404,
    "INVALID_RUNTIME_REF": 404,
    "MODEL_NOT_INSTALLED": 409,
    "MODEL_RESIDENT": 409,
    "MODEL_BUSY": 409,
    "ARCH_UNSUPPORTED": 409,
    "NETWORK_NOT_INTERNAL": 500,
}


def _found(value: dict[str, Any] | None) -> tuple[int, Any]:
    if value is None:
        return 404, {"error": {"code": "NOT_FOUND", "message": "Unknown runtime reference."}}
    return 200, value


class RequestHandler(BaseHTTPRequestHandler):
    server: DaemonServer
    protocol_version = "HTTP/1.1"
    server_version = "crewquarters-runtime"
    timeout = 60  # per-connection socket timeout: idle or slow clients cannot hold threads

    def address_string(self) -> str:
        return "unix"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s " + format, self.address_string(), *args)

    def _send(self, status: int, payload: Any) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send(status, {"error": {"code": code, "message": message}})

    def _dispatch(self, method: str) -> None:
        token = self.headers.get("Authorization", "")
        scheme, _, value = token.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            value.encode(), self.server.token.encode()
        ):
            self.close_connection = True  # the unread body must not become a request
            self._error(401, "UNAUTHENTICATED", "Service credential required.")
            return
        declared = self.headers.get("Content-Length") or "0"
        if not declared.isdigit():
            self.close_connection = True
            self._error(400, "INVALID_LENGTH", "Content-Length must be a non-negative integer.")
            return
        length = int(declared)
        if length > MAX_BODY:
            self.close_connection = True
            self._error(413, "PAYLOAD_TOO_LARGE", "Request body too large.")
            return
        raw = self.rfile.read(length) if length else b""
        parsed = urlparse(self.path)
        for route_method, pattern, handler in ROUTES:
            match = pattern.match(parsed.path)
            if match and route_method == method:
                try:
                    body = _json_body(raw)
                    status, payload = handler(
                        self.server.engine, body, parse_qs(parsed.query), list(match.groups())
                    )
                except SpecError as exc:
                    self._error(STATUS_FOR_CODE.get(exc.code, 422), exc.code, exc.message)
                except DockerError as exc:
                    log.warning("docker error on %s %s: %s", method, parsed.path, exc)
                    self._error(502, "DOCKER_ERROR", exc.message[:500])
                except (ValueError, TypeError) as exc:
                    self._error(422, "INVALID_REQUEST", str(exc)[:500])
                except Exception:
                    log.exception("unhandled error on %s %s", method, parsed.path)
                    self._error(500, "INTERNAL", "Unexpected runtime error.")
                else:
                    self._send(status, payload)
                return
        self._error(404, "NOT_FOUND", "No such route.")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")


class DaemonServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, engine: Engine, token: str, sock: socket.socket | None, path: Path) -> None:
        if len(token) < MIN_TOKEN_LENGTH:
            raise RuntimeError(
                f"The service token must be at least {MIN_TOKEN_LENGTH} characters; "
                "refusing to start."
            )
        self.engine = engine
        self.token = token
        if sock is not None:  # systemd socket activation
            socketserver.BaseServer.__init__(self, str(path), RequestHandler)
            self.socket = sock
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and stat.S_ISSOCK(path.stat().st_mode):
                path.unlink()
            super().__init__(str(path), RequestHandler)
            os.chmod(path, engine.cfg.socket_mode)


def systemd_socket() -> socket.socket | None:
    """Return the socket passed by systemd (LISTEN_FDS), if any."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    if int(os.environ.get("LISTEN_FDS", "0")) < 1:
        return None
    return socket.socket(fileno=3)
