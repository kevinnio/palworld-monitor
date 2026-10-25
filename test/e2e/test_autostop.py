"""End-to-end smoke test for the Palworld Monitor autostop feature.

Run with: uv run poe smoke -k autostop
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
import requests
import yaml

from .helpers import TEST_SETTINGS, run_monitor_app

pytestmark = pytest.mark.e2e


DUMMY_SCRIPT = str(Path(__file__).resolve().parent / "dummy" / "PalServer-Dummy.py")


@pytest.fixture(scope="module")
def running_dummy(app_process):
    """Start after monitor cleanup, so only auto-stop can end this dummy."""
    env = {**os.environ, "DUMMY_PLAYER_COUNT": "0"}
    proc = subprocess.Popen(
        [sys.executable, DUMMY_SCRIPT],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        assert proc.poll() is None, "Dummy exited before the auto-stop test"
        try:
            response = requests.get(
                "http://127.0.0.1:8212/v1/api/players",
                auth=("admin", "palworld123"),
                timeout=1,
            )
            if response.status_code == 200 and response.json() == {"players": []}:
                break
        except requests.RequestException:
            pass

        time.sleep(0.1)
    else:
        proc.kill()
        proc.wait(timeout=5)
        pytest.fail("Empty dummy never became healthy")

    yield proc
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        pass


@pytest.fixture(scope="module")
def app_process(tmp_path_factory):
    config = yaml.safe_load(TEST_SETTINGS.read_text())
    config["features"].update(autoStart=False, autoStop=True)
    config["autoStop"]["stopDelay"] = 5
    path = tmp_path_factory.mktemp("autostop") / "settings.yaml"
    path.write_text(yaml.safe_dump(config))
    with run_monitor_app(settings_path=path) as process:
        yield process


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="auto-stop process detection false positive on Linux",
)
def test_autostop(running_dummy, app_process):
    started = time.monotonic()
    assert running_dummy.poll() is None
    deadline = started + 60
    while time.monotonic() < deadline:
        try:
            proc = psutil.Process(running_dummy.pid)
            if not proc.is_running():
                assert time.monotonic() - started >= 30, (
                    "Dummy died before the startup guard"
                )
                return
        except psutil.NoSuchProcess:
            assert time.monotonic() - started >= 30, (
                "Dummy died before the startup guard"
            )
            return
        time.sleep(1)
    pytest.fail("Auto-stop did not kill the dummy PalServer within 60 s")
