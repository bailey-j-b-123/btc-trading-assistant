"""The developer guard must refuse network, live SQLite, and process control."""

import runpy
from pathlib import Path
import socket
import sqlite3
import sys

import pytest


@pytest.fixture
def guard(monkeypatch, tmp_path):
    hooks = []
    monkeypatch.setattr(sys, "addaudithook", hooks.append)
    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts/offline_pytest.py"))
    module["install_guard"](tmp_path / "checkout", tmp_path / "scratch")
    return hooks[0], tmp_path


def test_guard_refuses_external_effects(guard):
    hook, root = guard
    for event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.kill"):
        with pytest.raises(PermissionError):
            hook(event, ())
    with pytest.raises(PermissionError):
        hook("sqlite3.connect", (str(root / "live.sqlite3"),))
    with pytest.raises(PermissionError):
        hook("open", (str(root / "checkout/.env"), "r", 0))
    with pytest.raises(PermissionError):
        hook("open", (str(root / "outside.txt"), "w", 0))


def test_guard_allows_synthetic_state(guard):
    hook, root = guard
    hook("sqlite3.connect", (":memory:",))
    hook("sqlite3.connect", (str(root / "scratch/test.sqlite3"),))
    hook("sqlite3.connect", (f"file:{root}/scratch/test.sqlite3?mode=ro",))
    hook("open", (str(root / "checkout/.env.example"), "r", 0))


def test_active_guard_blocks_network_when_using_offline_runner():
    # Normal pytest remains supported; the guarded runner installs the hook.
    if not any("offline_pytest" in str(frame.filename) for frame in __import__("inspect").stack()):
        pytest.skip("Active-guard integration check requires scripts/offline_pytest.py")
    with pytest.raises(PermissionError, match="Offline test guard"):
        socket.getaddrinfo("example.invalid", 443)
    with pytest.raises(PermissionError, match="SQLite path"):
        sqlite3.connect("/brain-test-forbidden/live.sqlite3")
