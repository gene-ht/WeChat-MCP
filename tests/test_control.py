from __future__ import annotations

import json
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from wechat_mcp import control


class FakeService:
    def __init__(self) -> None:
        self.mode = "active"
        self.calls: list[str] = []

    def status(self):
        return {"mode": self.mode, "wechat_running": True}

    def set_mode(self, mode):
        self.calls.append(mode)
        self.mode = mode
        return self.status()


@pytest.fixture
def sock_dir():
    # AF_UNIX paths are limited to ~104 bytes; pytest tmp dirs are too long.
    directory = Path(tempfile.mkdtemp(prefix="wmcp-", dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def server(sock_dir):
    service = FakeService()
    path = control.socket_path(sock_dir)
    srv = control.ControlServer(service, path)
    srv.start()
    yield service, path
    srv.stop()
    srv.join(timeout=2)


def test_status_roundtrip(server):
    service, path = server
    response = control.request(path, {"cmd": "status"})
    assert response["ok"] is True
    assert response["status"]["mode"] == "active"


def test_set_mode_switches_running_server(server):
    service, path = server
    response = control.request(path, {"cmd": "set_mode", "mode": "paused"})
    assert response["ok"] is True
    assert service.mode == "paused"
    assert service.calls == ["paused"]


def test_unknown_mode_is_rejected(server):
    service, path = server
    response = control.request(path, {"cmd": "set_mode", "mode": "banana"})
    assert response["ok"] is False
    assert service.calls == []


def test_unknown_command_is_rejected(server):
    _, path = server
    response = control.request(path, {"cmd": "rm -rf"})
    assert response["ok"] is False


def test_request_returns_none_without_server(sock_dir):
    assert control.request(control.socket_path(sock_dir), {"cmd": "status"}) is None


def test_socket_is_owner_only(server):
    _, path = server
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_socket_is_removed_on_stop(sock_dir):
    path = control.socket_path(sock_dir)
    srv = control.ControlServer(FakeService(), path)
    srv.start()
    assert path.exists()
    srv.stop()
    srv.join(timeout=2)
    assert not path.exists()


def test_shutdown_calls_the_hook(sock_dir):
    stopped = {"called": False}

    def on_shutdown() -> None:
        stopped["called"] = True

    srv = control.ControlServer(FakeService(), control.socket_path(sock_dir), on_shutdown)
    srv.start()
    try:
        response = control.request(control.socket_path(sock_dir), {"cmd": "shutdown"})
    finally:
        srv.stop()
        srv.join(timeout=2)

    assert response["ok"] is True
    for _ in range(20):
        if stopped["called"]:
            break
        import time

        time.sleep(0.05)
    assert stopped["called"] is True


def test_malformed_request_gets_an_error(server):
    _, path = server
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    client.connect(str(path))
    client.sendall(b"not json\n")
    data = client.recv(4096)
    client.close()
    assert json.loads(data)["ok"] is False
