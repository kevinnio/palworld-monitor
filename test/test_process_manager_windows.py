"""
Tests for WindowsProcessManager.

These tests are Windows-specific and will be skipped on other platforms.
"""

import sys
import os
import pytest
import time
import psutil
from src.process_manager import WindowsProcessManager
from test.support import get_python_executable

# Skip all tests if not on Windows
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-specific tests"
)


class TestWindowsProcessManager:
    """Test suite for WindowsProcessManager."""

    def test_pid_file_name(self):
        """Test that PID file name is correct."""
        manager = WindowsProcessManager()
        assert manager.pid_file_name() == "palworld_server.win.pid"

    def test_clear_pid_forgets_process_and_file(self):
        """clear_pid must drop the tracked pid and the pid file, so the
        detection loop can look for a fresh process afterwards."""
        manager = WindowsProcessManager()
        manager.set_known_pid(999999)
        assert os.path.exists(manager.pid_file_name())
        manager.clear_pid()
        assert manager.launched_pid is None
        assert not os.path.exists(manager.pid_file_name())

    def test_launch_and_check_process(self, test_sleep_script):
        """Test launching a process and checking if it's running."""
        manager = WindowsProcessManager()

        # Ensure no existing PID
        manager.launched_pid = None

        # Use pythonw.exe on Windows to avoid console windows
        python_exe = get_python_executable()
        script_path = os.path.abspath(test_sleep_script)

        # Launch process
        manager.launch_process(python_exe, script_path)

        # Give it a moment to start
        time.sleep(1)

        # Check if running
        assert manager.is_process_running() is True, (
            "Process should be running after launch"
        )

        # Clean up
        manager.terminate_process()

    def test_terminate_process(self, test_sleep_script):
        """Test terminating a launched process."""
        manager = WindowsProcessManager()

        # Ensure no existing PID
        manager.launched_pid = None

        # Use pythonw.exe on Windows to avoid console windows
        python_exe = get_python_executable()
        script_path = os.path.abspath(test_sleep_script)

        # Launch process
        manager.launch_process(python_exe, script_path)
        time.sleep(1)

        # Verify it's running
        assert manager.is_process_running() is True, (
            "Process should be running after launch"
        )

        # Terminate
        result = manager.terminate_process()
        assert result is True

        # Give it a moment to terminate
        time.sleep(3)

        # Verify it's not running
        assert manager.is_process_running() is False, (
            "Process should not be running after termination"
        )
        assert manager.launched_pid is None

    def test_is_process_running_with_no_pid(self):
        """Test checking process status when no PID is set."""
        manager = WindowsProcessManager()
        manager.launched_pid = None
        assert manager.is_process_running() is False

    def test_terminate_process_with_no_pid(self):
        """Test terminating when no PID is set."""
        manager = WindowsProcessManager()
        manager.launched_pid = None
        result = manager.terminate_process()
        assert result is False

    def test_pid_file_persistence(self, test_sleep_script):
        """Test that PID is saved to and loaded from file."""
        manager1 = WindowsProcessManager()
        manager1.launched_pid = None

        # Use pythonw.exe on Windows to avoid console windows
        python_exe = get_python_executable()
        script_path = os.path.abspath(test_sleep_script)

        # Launch process
        manager1.launch_process(python_exe, script_path)
        time.sleep(1)

        pid1 = manager1.launched_pid
        assert pid1 is not None

        # Check PID file exists
        assert os.path.exists(manager1.pid_file_name())

        # Create new manager instance - should load PID from file
        manager2 = WindowsProcessManager()
        assert manager2.launched_pid == pid1

        # Clean up
        manager2.terminate_process()

    def test_set_known_pid(self):
        """Test setting a known PID."""
        manager = WindowsProcessManager()

        # Set a known PID (use current process PID as test)
        test_pid = os.getpid()
        manager.set_known_pid(test_pid)

        assert manager.launched_pid == test_pid
        assert os.path.exists(manager.pid_file_name())

        # Verify it was saved to file
        with open(manager.pid_file_name(), "r") as f:
            saved_pid = int(f.read().strip())
            assert saved_pid == test_pid

    def test_find_process_pid(self):
        """Test finding a process by name."""
        manager = WindowsProcessManager()

        # Find Python process (should exist since we're running Python)
        pid = manager.find_process_pid("python")
        assert pid is not None

        # Verify it's a valid PID
        try:
            process = psutil.Process(pid)
            assert process.is_running()
        except psutil.NoSuchProcess:
            pytest.fail("Found PID is not a running process")

    def test_find_process_pid_not_found(self):
        """Test finding a process that doesn't exist."""
        manager = WindowsProcessManager()
        pid = manager.find_process_pid("nonexistent_process_xyz123")
        assert pid is None

    def test_terminate_nonexistent_process(self):
        """Test terminating a process that doesn't exist."""
        manager = WindowsProcessManager()
        # Set PID to a very high number that won't exist
        manager.launched_pid = 99999999
        result = manager.terminate_process()
        # Should return False and clean up
        assert result is False
        assert manager.launched_pid is None

    def test_is_process_running_with_dead_pid(self):
        """Test checking status when PID file exists but process is dead."""
        manager = WindowsProcessManager()
        # Set PID to a very high number that won't exist
        manager.launched_pid = 99999999
        assert manager.is_process_running() is False
