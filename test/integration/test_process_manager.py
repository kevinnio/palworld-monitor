"""Shared process backend contracts, run against the host's native backend."""

import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from src.lifecycle.messages import ProcessIdentity
from src.process_manager import LinuxProcessManager, WindowsProcessManager
from test.support import get_python_executable


@pytest.fixture(
    params=[
        pytest.param(
            (LinuxProcessManager, "palworld_server.linux.pid"),
            id="linux",
            marks=[
                pytest.mark.linux,
                pytest.mark.skipif(sys.platform != "linux", reason="Linux backend"),
            ],
        ),
        pytest.param(
            (WindowsProcessManager, "palworld_server.win.pid"),
            id="windows",
            marks=[
                pytest.mark.windows,
                pytest.mark.skipif(sys.platform != "win32", reason="Windows backend"),
            ],
        ),
    ]
)
def backend(request):
    return request.param


@pytest.fixture
def manager(backend, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return backend[0]()


@pytest.fixture
def running_server(manager, test_sleep_script):
    identity = manager.launch_process(
        get_python_executable(), str(Path(test_sleep_script).resolve())
    )
    assert identity is not None
    process = psutil.Process(identity.pid)
    try:
        yield identity
    finally:
        # Clean up even when an assertion fails or the backend stop is broken.
        if process.is_running():
            process.kill()
            process.wait(timeout=5)


def test_pid_file_name(manager, backend):
    assert manager.pid_file_name() == backend[1]


def test_clear_identity_forgets_process_and_file(manager):
    identity = ProcessIdentity(999999, 1.0)
    manager.set_known_identity(identity)
    assert Path(manager.pid_file_name()).exists()

    assert manager.clear_identity(identity) is True
    assert manager.tracked_identity is None
    assert not Path(manager.pid_file_name()).exists()


def test_launch_and_check_process(manager, running_server):
    assert manager.identity_is_running(running_server) is True
    assert manager.tracked_identity == running_server


def test_terminate_process(manager, running_server):
    assert manager.identity_is_running(running_server) is True

    assert manager.terminate_process(running_server) is True
    assert manager.identity_is_running(running_server) is False
    assert manager.tracked_identity is None
    assert not Path(manager.pid_file_name()).exists()


def test_identity_is_running_with_no_identity(manager):
    assert manager.identity_is_running(None) is False
    assert manager.observe().tracked_running is False


def test_terminate_process_with_no_pid(manager):
    assert manager.terminate_process() is False


def test_pid_file_persistence(manager, running_server, backend):
    assert Path(manager.pid_file_name()).read_text() == str(running_server.pid)
    reloaded = backend[0]()
    assert reloaded.tracked_identity == running_server

    assert reloaded.terminate_process(running_server) is True
    assert not manager.identity_is_running(running_server)


def test_set_known_identity(manager):
    identity = manager._identity_for_pid(os.getpid())
    manager.set_known_identity(identity)

    assert manager.tracked_identity == identity
    assert Path(manager.pid_file_name()).read_text() == str(identity.pid)


def test_find_process_identity(manager):
    identity = manager.find_process_identity("python")
    assert identity is not None
    assert psutil.Process(identity.pid).is_running()


def test_find_process_identity_not_found(manager):
    assert manager.find_process_identity("nonexistent_process_xyz123") is None


def test_terminate_nonexistent_process(manager):
    identity = ProcessIdentity(99999999, 1.0)
    manager.set_known_identity(identity)

    assert manager.terminate_process(identity) is True
    assert manager.tracked_identity is None


def test_identity_is_running_with_dead_pid(manager):
    assert manager.identity_is_running(ProcessIdentity(99999999, None)) is False


@pytest.mark.skipif(sys.platform != "linux", reason="Linux zombie process semantics")
def test_exited_child_is_not_running_before_it_is_reaped(manager):
    with subprocess.Popen([sys.executable, "-c", "pass"]) as process:
        # Observe exit without reaping so the backend sees a real zombie.
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        assert psutil.Process(process.pid).status() == psutil.STATUS_ZOMBIE
        identity = manager._identity_for_pid(process.pid)

        assert manager.identity_is_running(identity) is False
