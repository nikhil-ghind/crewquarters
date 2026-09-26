"""Run the fake platform in a background thread (for tests and `crewctl test`)."""

from __future__ import annotations

import threading
import time
from types import TracebackType

import uvicorn
from fastapi import FastAPI


class BackgroundServer:
    def __init__(self, app: FastAPI, host: str = "127.0.0.1", port: int = 0) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.url = ""
        self._server = uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on")
        )
        self._thread = threading.Thread(target=self._server.run, name="crewq-fake", daemon=True)

    def __enter__(self) -> BackgroundServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the fake platform server did not start")
            time.sleep(0.01)
        socket = self._server.servers[0].sockets[0]
        self.port = int(socket.getsockname()[1])
        self.url = f"http://{self.host}:{self.port}"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
