"""Shared helpers and constants for e2e smoke tests."""

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import psutil
import requests

from src.settings import settings

TEST_SETTINGS = Path(__file__).resolve().parent / "settings.yaml"
settings.readSettings(TEST_SETTINGS)

BASE_URL = "http://localhost:8213"


def _pids_listening_on_port(port):
    """Return PIDs of processes listening on the given TCP port."""
    pids = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
                if conn.pid:
                    pids.add(conn.pid)
    except (psutil.AccessDenied, Exception):
        pass
    return pids


def kill_tree(pid):
    """Kill a process and its entire child tree."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            child.kill()
        parent.kill()
        psutil.wait_procs(children + [parent], timeout=5)
    except psutil.NoSuchProcess:
        pass
    except Exception:
        pass


def free_port(port, timeout=10):
    """Kill anything listening on `port` and wait for it to free up."""
    for pid in _pids_listening_on_port(port):
        kill_tree(pid)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pids_listening_on_port(port):
            return True
        time.sleep(0.25)
    return False


def kill_existing_palworld():
    """Terminate any running Palworld server processes."""
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            cmdline = " ".join(info["cmdline"] or []).lower()
            if "palserver" in name or "palserver" in cmdline:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(1)


def remove_stale_pid_files():
    """Remove leftover PID files so the fresh app doesn't adopt a stale PID."""
    project_root = Path(__file__).resolve().parents[2]
    for name in ("palworld_server.win.pid", "palworld_server.linux.pid"):
        try:
            (project_root / name).unlink()
        except FileNotFoundError:
            pass


def is_palworld_running():
    """Check if any Palworld server process is running."""
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = proc.info["cmdline"] or []
            if "palserver" in name:
                return True
            if any("palserver" in (arg or "").lower() for arg in cmdline):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False


@contextmanager
def run_monitor_app(settings_path=TEST_SETTINGS):
    """Start the monitor app, yield the process, then tear it down."""
    web_port = settings.webServerPort
    base_url = f"http://localhost:{web_port}"
    if not free_port(web_port):
        raise RuntimeError(f"Could not free web port {web_port} for the test app")
    kill_existing_palworld()
    remove_stale_pid_files()

    project_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PALWORLD_MONITOR_SETTINGS": str(settings_path)}
    process = subprocess.Popen(
        [sys.executable, "-m", "src.main"],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    deadline = time.time() + 30
    last_error = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"App process exited early with code {process.returncode}"
            )
        try:
            response = requests.get(f"{base_url}/login", timeout=1)
            if response.status_code == 200:
                break
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(0.5)
    else:
        kill_tree(process.pid)
        raise RuntimeError(f"App did not start in time: {last_error}")

    try:
        yield process
    finally:
        kill_tree(process.pid)
        try:
            process.wait(timeout=5)
        except Exception:
            pass
        kill_existing_palworld()
        remove_stale_pid_files()
        free_port(web_port, timeout=5)
        for pid in _pids_listening_on_port(settings.palworldRESTPort):
            kill_tree(pid)
