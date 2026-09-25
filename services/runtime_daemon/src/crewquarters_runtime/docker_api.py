"""Minimal Docker Engine API client over the local Unix socket (no dependencies)."""

from __future__ import annotations

import http.client
import json
import socket
import struct
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class DockerError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Docker API {status}: {message}")
        self.status = status
        self.message = message


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: float) -> None:
        super().__init__("docker", timeout=timeout)
        self._socket_path = str(socket_path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class DockerClient:
    def __init__(self, socket_path: Path, timeout: float = 60.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    # --- transport ------------------------------------------------------------------

    def _open(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
    ) -> tuple[_UnixHTTPConnection, http.client.HTTPResponse]:
        if query:
            params = {k: v for k, v in query.items() if v is not None}
            path = f"{path}?{urllib.parse.urlencode(params)}"
        conn = _UnixHTTPConnection(self.socket_path, timeout or self.timeout)
        headers = {"Host": "docker"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        return conn, conn.getresponse()

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
        ok: tuple[int, ...] = (200, 201, 204, 304),
    ) -> Any:
        conn, response = self._open(method, path, query, body, timeout)
        try:
            data = response.read()
        finally:
            conn.close()
        if response.status not in ok:
            try:
                message = json.loads(data).get("message", "")
            except (ValueError, AttributeError):
                message = data.decode(errors="replace")[:500]
            raise DockerError(response.status, message)
        if not data:
            return None
        if response.getheader("Content-Type", "").startswith("application/json"):
            return json.loads(data)
        return data

    def stream_json(
        self, method: str, path: str, query: dict[str, Any] | None = None, timeout: float = 3600
    ) -> Iterator[dict[str, Any]]:
        conn, response = self._open(method, path, query, None, timeout)
        try:
            if response.status != 200:
                data = response.read()
                try:
                    message = json.loads(data).get("message", "")
                except ValueError:
                    message = data.decode(errors="replace")[:500]
                raise DockerError(response.status, message)
            buffer = b""
            while True:
                chunk = (
                    response.read1(65536) if hasattr(response, "read1") else response.read(65536)
                )
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        yield json.loads(line)
        finally:
            conn.close()

    # --- system ---------------------------------------------------------------------

    def ping(self) -> bool:
        return bool(self.request("GET", "/_ping") == b"OK")

    def info(self) -> dict[str, Any]:
        return dict(self.request("GET", "/info"))

    def version(self) -> dict[str, Any]:
        return dict(self.request("GET", "/version"))

    # --- images ---------------------------------------------------------------------

    def image_inspect(self, ref: str) -> dict[str, Any] | None:
        try:
            return dict(self.request("GET", f"/images/{urllib.parse.quote(ref, safe='')}/json"))
        except DockerError as exc:
            if exc.status == 404:
                return None
            raise

    def image_pull(self, ref: str) -> Iterator[dict[str, Any]]:
        for event in self.stream_json("POST", "/images/create", {"fromImage": ref}):
            if "error" in event:
                raise DockerError(500, str(event.get("error")))
            yield event

    # --- networks -------------------------------------------------------------------

    def network_inspect(self, name: str) -> dict[str, Any] | None:
        try:
            return dict(self.request("GET", f"/networks/{urllib.parse.quote(name)}"))
        except DockerError as exc:
            if exc.status == 404:
                return None
            raise

    def network_create(
        self,
        name: str,
        *,
        internal: bool,
        labels: dict[str, str],
        options: dict[str, str] | None = None,
    ) -> None:
        body = {"Name": name, "Driver": "bridge", "Internal": internal, "Labels": labels}
        if options:
            body["Options"] = options
        self.request("POST", "/networks/create", body=body, ok=(201,))

    # --- containers -----------------------------------------------------------------

    def container_create(self, name: str, config: dict[str, Any]) -> str:
        created = self.request("POST", "/containers/create", {"name": name}, body=config, ok=(201,))
        return str(created["Id"])

    def container_start(self, ident: str) -> None:
        self.request("POST", f"/containers/{ident}/start", ok=(204, 304))

    def container_inspect(self, ident: str) -> dict[str, Any] | None:
        try:
            return dict(self.request("GET", f"/containers/{urllib.parse.quote(ident)}/json"))
        except DockerError as exc:
            if exc.status == 404:
                return None
            raise

    def container_stop(self, ident: str, grace_seconds: int) -> None:
        try:
            self.request(
                "POST",
                f"/containers/{ident}/stop",
                {"t": grace_seconds},
                timeout=grace_seconds + 30,
                ok=(204, 304),
            )
        except DockerError as exc:
            if exc.status != 404:
                raise

    def container_remove(self, ident: str) -> None:
        try:
            self.request(
                "DELETE", f"/containers/{ident}", {"force": "true", "v": "true"}, ok=(204,)
            )
        except DockerError as exc:
            if exc.status not in (404, 409):
                raise

    def container_list(self, labels: dict[str, str]) -> list[dict[str, Any]]:
        filters = {"label": [f"{k}={v}" for k, v in labels.items()]}
        result = self.request(
            "GET", "/containers/json", {"all": "true", "filters": json.dumps(filters)}
        )
        return list(result or [])

    def container_logs(self, ident: str, tail: int) -> list[tuple[str, str]]:
        """Return up to ``tail`` (stream, line) pairs from a non-TTY container."""
        raw = self.request(
            "GET",
            f"/containers/{ident}/logs",
            {"stdout": "true", "stderr": "true", "tail": tail, "timestamps": "true"},
        )
        return list(_demux(raw or b""))


def _demux(raw: bytes) -> Iterator[tuple[str, str]]:
    """Split Docker's multiplexed log stream (8-byte frame headers) into lines."""
    offset = 0
    names = {1: "stdout", 2: "stderr"}
    while offset + 8 <= len(raw):
        stream, size = struct.unpack(">BxxxL", raw[offset : offset + 8])
        offset += 8
        chunk = raw[offset : offset + size].decode(errors="replace")
        offset += size
        for line in chunk.splitlines():
            yield names.get(stream, "stdout"), line
