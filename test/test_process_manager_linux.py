"""
Tests for LinuxProcessManager.

These tests are Linux-specific and will be skipped on other platforms.
"""

import sys
import os
import pytest
import time
import psutil
from src.lifecycle.messages import ProcessIdentity
from src.process_manager import LinuxProcessManager
from test.support import get_python_executable

# Skip all tests if not on Linux
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux-specific tests")


class TestLinuxProcessManager:
    """Test suite for LinuxProcessManager."""

    def test_pid_file_name(self):
        """Test that PID file name is correct."""
        manager = LinuxProcessManager()
        assert manager.pid_file_name() == "palworld_server.linux.pid"

    def test_launch_and_check_process(self, test_sleep_script):
        """Test launching a process and checking if it's running."""
        manager = LinuxProcessManager()
        manager.clear_identity(manager.tracked_identity)

        python_exe = get_python_executable()
        script_path = os.path.abspath(test_sleep_script)

        identity = manager.launch_process(python_exe, script_path)
        assert identity is not None
        time.sleep(1)

        assert manager.identity_is_running(identity) is True, (
            "Process should be running after launch"
        )

        manager.terminate_process(identity)

    def test_terminate_process(self, test_sleep_script):
        """Test terminating a launched process."""
        manager = LinuxProcessManager()
        manager.clear_identity(manager.tracked_identity)

        python_exe = get_python_executable()
        script_path = os.path.abspath(test_sleep_script)

        identity = manager.launch_process(python_exe, script_path)
        time.sleep(1)

        assert manager.identity_is_running(identity) is True, (
            "Process should be running after launch"
        )

        result = manager.terminate_process(identity)
        assert result is True

        time.sleep(1)

        assert manager.identity_is_running(identity) is False, (
            "Process should not be running after termination"
        )
        assert manager.tracked_identity is None

    def test_identity_is_running_with_no_identity(self):
        """Test checking process status when no identity is set."""
        manager = LinuxProcessManager()
        manager.clear_identity(manager.tracked_identity)
        assert manager.identity_is_running(None) is False
        assert manager.observe().tracked_running is False

    def test_terminate_process_with_no_pid(self):
        """Test terminating when no identity is set."""
        manager = LinuxProcessManager()
        manager.clear_identity(manager.tracked_identity)
        assert manager.terminate_process() is False

    def test_pid_file_persistence(self, test_sleep_script):
        """Test that the PID is saved to and loaded from file."""
        manager1 = LinuxProcessManager()
        manager1.clear_identity(manager1.tracked_identity)

        python_exe = get_python_executable()
        script_path = os.path.abspath(test_sleep_script)

        identity = manager1.launch_process(python_exe, script_path)
        time.sleep(1)

        assert identity is not None
        assert os.path.exists(manager1.pid_file_name())

        manager2 = LinuxProcessManager()
        assert manager2.tracked_identity.pid == identity.pid

        manager2.terminate_process(identity)

    def test_set_known_identity(self):
        """Test recording a known process identity."""
        manager = LinuxProcessManager()

        test_pid = os.getpid()
        identity = manager._identity_for_pid(test_pid)
        manager.set_known_identity(identity)

        assert manager.tracked_identity == identity
        assert os.path.exists(manager.pid_file_name())

        with open(manager.pid_file_name(), "r") as f:
            saved_pid = int(f.read().strip())
            assert saved_pid == test_pid

    def test_find_process_identity(self):
        """Test finding a process by name."""
        manager = LinuxProcessManager()

        identity = manager.find_process_identity("python")
        assert identity is not None

        try:
            process = psutil.Process(identity.pid)
            assert process.is_running()
        except psutil.NoSuchProcess:
            pytest.fail("Found PID is not a running process")

    def test_find_process_identity_not_found(self):
        """Test finding a process that doesn't exist."""
        manager = LinuxProcessManager()
        assert manager.find_process_identity("nonexistent_process_xyz123") is None

    def test_terminate_nonexistent_process(self):
        """A process that is already gone is a confirmed termination."""
        manager = LinuxProcessManager()
        identity = ProcessIdentity(99999999, 1.0)
        manager.set_known_identity(identity)

        result = manager.terminate_process(identity)

        assert result is True
        assert manager.tracked_identity is None

    def test_identity_is_running_with_dead_pid(self):
        """Test checking status when the PID belongs to a dead process."""
        manager = LinuxProcessManager()
        assert manager.identity_is_running(ProcessIdentity(99999999, None)) is False
