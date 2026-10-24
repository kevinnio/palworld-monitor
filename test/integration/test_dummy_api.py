"""Verify the dummy PalServer API matches the Real PalServer contract.

Starts the dummy process directly and exercises all endpoints.
Run with: uv run poe test test/integration/test_dummy_api.py
"""

import socket
import subprocess
import sys
from pathlib import Path

import psutil
import requests
from requests.auth import HTTPBasicAuth

import pytest

from test.support import wait_until

DUMMY_SCRIPT = str(
    Path(__file__).resolve().parents[1] / "e2e" / "dummy" / "PalServer-Dummy.py"
)

AUTH = HTTPBasicAuth("admin", "palworld123")
BASE = "http://127.0.0.1:8212/v1/api"


@pytest.fixture(scope="module")
def dummy_process():
    """Start the dummy server directly, tear it down after."""
    with socket.socket() as listener:
        listener.settimeout(1)
        if listener.connect_ex(("127.0.0.1", 8212)) == 0:
            pytest.skip("Dummy server port 8212 is already in use")

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", 8212))
        except OSError:
            pytest.skip("Dummy server port 8212 is already in use")

    proc = subprocess.Popen(
        [sys.executable, DUMMY_SCRIPT],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process = psutil.Process(proc.pid)
    try:

        def ready():
            assert proc.poll() is None, "Dummy server exited during startup"
            try:
                return (
                    requests.get(f"{BASE}/info", auth=AUTH, timeout=1).status_code
                    == 200
                )
            except requests.ConnectionError:
                return False

        wait_until(ready, message="Dummy API did not become ready")
        yield proc
    finally:
        if process.is_running():
            children = process.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            proc.kill()
            psutil.wait_procs(children, timeout=5)
        proc.wait(timeout=5)


def test_get_players(dummy_process):
    r = requests.get(f"{BASE}/players", auth=AUTH, timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert "players" in data
    assert len(data["players"]) >= 3
    for p in data["players"]:
        for key in ("name", "playerId", "userId", "level"):
            assert key in p, f"Missing '{key}'"


def test_get_metrics(dummy_process):
    r = requests.get(f"{BASE}/metrics", auth=AUTH, timeout=5)
    assert r.status_code == 200
    m = r.json()
    for key in (
        "serverfps",
        "currentplayernum",
        "serverframetime",
        "maxplayernum",
        "uptime",
        "basecampnum",
        "days",
    ):
        assert key in m, f"Missing '{key}' in metrics"


def test_get_info(dummy_process):
    r = requests.get(f"{BASE}/info", auth=AUTH, timeout=5)
    assert r.status_code == 200
    m = r.json()
    assert "servername" in m
    assert "version" in m


def test_get_settings(dummy_process):
    r = requests.get(f"{BASE}/settings", auth=AUTH, timeout=5)
    assert r.status_code == 200
    m = r.json()
    assert "servername" in m


def test_kick(dummy_process):
    # Kick steam_001.
    r = requests.post(
        f"{BASE}/kick", json={"userid": "steam_001"}, auth=AUTH, timeout=5
    )
    assert r.status_code == 200
    assert r.content == b""
    # Verify removed.
    r = requests.get(f"{BASE}/players", auth=AUTH, timeout=5)
    ids = {p["playerId"] for p in r.json()["players"]}
    assert "steam_001" not in ids


def test_ban(dummy_process):
    r = requests.post(f"{BASE}/ban", json={"userid": "steam_002"}, auth=AUTH, timeout=5)
    assert r.status_code == 200
    assert r.content == b""
    r = requests.get(f"{BASE}/players", auth=AUTH, timeout=5)
    ids = {p["playerId"] for p in r.json()["players"]}
    assert "steam_002" not in ids


def test_unban(dummy_process):
    r = requests.post(
        f"{BASE}/unban", json={"userid": "steam_003"}, auth=AUTH, timeout=5
    )
    assert r.status_code == 200
    assert r.content == b""


def test_announce(dummy_process):
    r = requests.post(
        f"{BASE}/announce", json={"message": "hello"}, auth=AUTH, timeout=5
    )
    assert r.status_code == 200


def test_auth_required(dummy_process):
    r = requests.get(f"{BASE}/players", timeout=5)
    assert r.status_code == 401
    r = requests.post(f"{BASE}/kick", json={"userid": "x"}, timeout=5)
    assert r.status_code == 401
