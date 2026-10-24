"""End-to-end smoke test for the Palworld Monitor autostop feature.

Run with: uv run poe smoke -k autostop
"""

import os
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from src.lifecycle.auto_stop import AutoStopConfig
from src.settings import settings
from .helpers import BASE_URL, kill_existing_palworld, kill_tree

pytestmark = pytest.mark.e2e


DUMMY_SCRIPT = str(Path(__file__).resolve().parent / "dummy" / "PalServer-Dummy.py")


def test_autostop(app_process):
    """Observe a healthy empty server through RUNNING, STOPPING and STOPPED."""
    with requests.Session() as session:
        login = session.post(
            f"{BASE_URL}/login",
            data={"username": settings.webUsername, "password": settings.webPassword},
            allow_redirects=False,
            timeout=5,
        )
        assert login.status_code == 302
        kill_existing_palworld()

        with session.get(
            f"{BASE_URL}/stream", stream=True, timeout=(5, 60)
        ) as response:
            response.raise_for_status()
            snapshots = (
                json.loads(line[6:])["data"]
                for line in response.iter_lines(chunk_size=1)
                if line.startswith(b"data: ")
            )
            deadline = time.monotonic() + 30
            for snapshot in snapshots:
                assert app_process.poll() is None, "Monitor exited before startup"
                assert time.monotonic() < deadline, "Monitor did not become STOPPED"
                if snapshot["state"] == "STOPPED":
                    break
            else:
                pytest.fail("Status stream closed before STOPPED")

            # App setup and its destructive cleanup have already finished.
            started_at = time.monotonic()
            proc = subprocess.Popen(
                [sys.executable, DUMMY_SCRIPT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "DUMMY_PLAYER_COUNT": "0"},
            )
            saw_empty_roster = False
            saw_stopping = False
            # Reap adopted Linux children while the monitor waits for their exit.
            reaper = threading.Thread(target=proc.wait, daemon=True)
            try:
                reaper.start()
                for snapshot in snapshots:
                    assert app_process.poll() is None, "Monitor exited during auto-stop"
                    assert time.monotonic() - started_at < 75, "Auto-stop timed out"
                    if not saw_empty_roster:
                        assert proc.poll() is None, (
                            "Dummy exited before a healthy roster"
                        )

                    if (
                        snapshot["state"] == "RUNNING"
                        and snapshot["playerData"] == "ok"
                        and snapshot["playerCount"] == 0
                    ):
                        saw_empty_roster = True
                    elif snapshot["state"] == "STOPPING":
                        assert saw_empty_roster, (
                            "Stopped without a healthy empty roster"
                        )
                        saw_stopping = True
                    elif snapshot["state"] == "STOPPED" and saw_empty_roster:
                        assert saw_stopping, "Dummy exited without an automatic stop"
                        proc.wait(timeout=10)
                        guard = AutoStopConfig(
                            True, settings.autoStopDelay
                        ).startup_guard
                        assert time.monotonic() - started_at >= guard
                        return

                pytest.fail("Status stream closed before auto-stop completed")
            finally:
                if proc.poll() is None:
                    kill_tree(proc.pid)
                proc.wait(timeout=5)
                reaper.join(timeout=5)
