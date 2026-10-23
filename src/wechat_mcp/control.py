"""Local control channel for a running server.

The MCP tools are the agent's way to switch modes. This module gives the human
a way that does not go through the model: a Unix socket in the state directory
that the CLI (``wechat-mcp --set-mode ...``) can talk to.

Protocol: one JSON object per line.

    -> {"cmd": "set_mode", "mode": "paused"}
    <- {"ok": true, "status": {...}}
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .logging_config import logger
from .models import try_parse_mode

SOCKET_NAME = "control.sock"
_MAX_REQUEST_BYTES = 4096


def socket_path(state_dir: Path) -> Path:
    path = Path(state_dir) / SOCKET_NAME
    if len(str(path)) > 100:
        raise ValueError(
            f"State directory is too deep for a Unix socket ({len(str(path))} bytes): "
            f"{path}. Set WECHAT_MCP_STATE_DIR to a shorter path."
        )
    return path


class ControlServer(threading.Thread):
    """Serve mode changes on a Unix socket, one request per connection."""

    def __init__(
        self,
        service: Any,
        path: Path,
        on_shutdown: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(name="wechat-mcp-control", daemon=True)
        self._service = service
        self._path = Path(path)
        self._on_shutdown = on_shutdown or (lambda: os._exit(0))
        self._sock: socket.socket | None = None
        self._stopping = threading.Event()

    def start(self) -> None:  # noqa: D102 - bind before the thread starts
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            # Only remove a socket nobody is listening on.
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            if probe.connect_ex(str(self._path)) == 0:
                probe.close()
                raise RuntimeError(f"Another control server is already at {self._path}")
            probe.close()
            self._path.unlink(missing_ok=True)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self._path))
        os.chmod(self._path, 0o600)
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        logger.info("Control socket listening at %s", self._path)
        super().start()

    def run(self) -> None:
        assert self._sock is not None
        while not self._stopping.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                self._handle(conn)

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(2.0)
            raw = conn.recv(_MAX_REQUEST_BYTES)
            request = json.loads(raw.decode("utf-8") or "{}")
            response = self._dispatch(request)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            response = {"ok": False, "error": f"bad request: {exc}"}
        with contextlib.suppress(OSError):
            conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode())

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        cmd = request.get("cmd")
        if cmd == "status":
            return {"ok": True, "status": self._service.status()}
        if cmd == "shutdown":
            # Reply first, then clean up and exit so the caller sees a clean
            # acknowledgement and no stale socket is left behind.
            threading.Timer(0.2, self._shutdown).start()
            logger.info("Shutdown requested via control socket")
            return {"ok": True, "stopping": True}
        if cmd == "set_mode":
            mode = try_parse_mode(request.get("mode"))
            if mode is None:
                return {"ok": False, "error": f"unknown mode {request.get('mode')!r}"}
            status = self._service.set_mode(mode.value)
            logger.info("Mode switched to %s via control socket", mode.value)
            return {"ok": True, "status": status}
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    def _shutdown(self) -> None:
        self.stop()
        self._on_shutdown()

    def stop(self) -> None:
        self._stopping.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        self._path.unlink(missing_ok=True)


def request(path: Path, payload: dict[str, Any], timeout: float = 1.5) -> dict[str, Any] | None:
    """Send one command to a running server; ``None`` if nothing is listening."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        if client.connect_ex(str(path)) != 0:
            return None
        client.sendall((json.dumps(payload) + "\n").encode())
        data = client.recv(_MAX_REQUEST_BYTES)
        return json.loads(data.decode("utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        client.close()
