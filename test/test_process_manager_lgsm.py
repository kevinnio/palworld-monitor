"""
Tests for LGSMProcessManager.

These are unit tests that mock subprocess/psutil calls rather than
launching a real LGSM script.
"""

import os
import time
from unittest.mock import MagicMock, patch

import psutil
import pytest

from src.events import Event
from src.process_manager import LGSMProcessManager


@pytest.fixture
def manager():
    m = LGSMProcessManager()
    m.launched_pid = None
    # Keep the tests fast: real timeouts default to 30s+.
    m.STARTUP_TIMEOUT = 0.2
    m.STARTUP_POLL_INTERVAL = 0.01
    m.SHUTDOWN_VERIFY_TIMEOUT = 0.2
    m.SHUTDOWN_VERIFY_POLL_INTERVAL = 0.01
    m.SHUTDOWN_WATCHDOG_POLL_INTERVAL = 0.01
    return m


class TestLGSMProcessManager:
    def test_pid_file_name(self, manager):
        assert manager.pid_file_name() == "palworld_server.lgsm.pid"

    @patch("src.events.bus.publish")
    @patch("src.process_manager.subprocess.run")
    def test_launch_process_success(self, mock_run, mock_publish, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        manager.find_process_pid = MagicMock(return_value=4242)

        manager.launch_process("/home/gameserver/pwserver/pwserver", "")

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["/home/gameserver/pwserver/pwserver", "start"]
        assert manager.launched_pid == 4242
        assert manager._exe_path == "/home/gameserver/pwserver/pwserver"
        mock_publish.assert_any_call("SERVER_STARTED", {"pid": 4242})

        # PID file should have been written
        assert os.path.exists(manager.pid_file_name())
        with open(manager.pid_file_name(), "r") as f:
            assert f.read().strip() == "4242"
        os.remove(manager.pid_file_name())

    @patch("src.events.bus.publish")
    @patch("src.process_manager.subprocess.run")
    def test_launch_process_start_command_fails(self, mock_run, mock_publish, manager):
        mock_run.side_effect = Exception("script not found")
        manager.find_process_pid = MagicMock(return_value=4242)

        manager.launch_process("/does/not/exist", "")

        assert manager.launched_pid is None
        mock_publish.assert_not_called()

    @patch("src.events.bus.publish")
    @patch("src.process_manager.subprocess.run")
    def test_launch_process_no_pid_found(self, mock_run, mock_publish, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        manager.find_process_pid = MagicMock(return_value=None)

        manager.launch_process("/home/gameserver/pwserver/pwserver", "")

        assert manager.launched_pid is None
        mock_publish.assert_not_called()

    @patch("src.events.bus.publish")
    @patch("src.process_manager.psutil.pid_exists")
    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_success(self, mock_run, mock_pid_exists, mock_publish, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_pid_exists.return_value = False
        manager.launched_pid = 4242
        manager._exe_path = "/home/gameserver/pwserver/pwserver"
        manager._save_pid_to_file(4242)

        result = manager.terminate_process()

        assert result is True
        assert manager.launched_pid is None
        assert not os.path.exists(manager.pid_file_name())
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["/home/gameserver/pwserver/pwserver", "stop"]
        assert kwargs["timeout"] == manager.LGSM_COMMAND_TIMEOUT
        mock_publish.assert_any_call("SERVER_STOPPED", {"pid": 4242})

    @patch("src.process_manager.psutil.pid_exists")
    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_stop_command_fails_falls_back_to_watchdog(
        self, mock_run, mock_pid_exists, manager
    ):
        """A failed/timed-out LGSM invocation doesn't necessarily mean the
        server isn't shutting down -- we still watch the pid rather than
        giving up outright."""
        mock_run.side_effect = Exception("boom")
        mock_pid_exists.return_value = True
        manager.launched_pid = 4242
        manager._exe_path = "/home/gameserver/pwserver/pwserver"

        result = manager.terminate_process()

        assert result is False
        # Not resolved synchronously -- a background watchdog took over.
        assert manager.launched_pid == 4242

    @patch("src.process_manager.psutil.pid_exists")
    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_still_running_after_timeout(
        self, mock_run, mock_pid_exists, manager
    ):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_pid_exists.return_value = True
        manager.launched_pid = 4242
        manager._exe_path = "/home/gameserver/pwserver/pwserver"

        result = manager.terminate_process()

        assert result is False
        assert manager.launched_pid == 4242

    @patch("src.events.bus.publish")
    @patch("src.process_manager.psutil.pid_exists")
    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_watchdog_confirms_stop_after_sync_timeout(
        self, mock_run, mock_pid_exists, mock_publish, manager
    ):
        """Regression test: even if the process outlives the synchronous
        verify window, the background watchdog must eventually publish
        SERVER_STOPPED once it actually exits -- otherwise the auto-start
        listener (which only re-arms on that event) gets stuck forever."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        start = time.time()
        # "Alive" through the synchronous verify window, "exits" during the
        # watchdog phase.
        mock_pid_exists.side_effect = lambda pid: (time.time() - start) < 0.35
        manager.launched_pid = 4242
        manager._exe_path = "/home/gameserver/pwserver/pwserver"
        manager._save_pid_to_file(4242)

        result = manager.terminate_process()

        assert result is False
        assert manager.launched_pid == 4242  # not resolved synchronously

        deadline = time.time() + 3
        while manager.launched_pid is not None and time.time() < deadline:
            time.sleep(0.02)

        assert manager.launched_pid is None
        assert not os.path.exists(manager.pid_file_name())
        mock_publish.assert_any_call("SERVER_STOPPED", {"pid": 4242})

    def test_terminate_process_with_no_pid(self, manager):
        manager.launched_pid = None
        assert manager.terminate_process() is False

    @patch("src.process_manager.psutil.pid_exists")
    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_falls_back_to_settings_exe_path(
        self, mock_run, mock_pid_exists, manager
    ):
        """If the manager never launched the server itself in this process
        (e.g. monitor restarted and re-attached to an already-running LGSM
        server), it should still know how to stop it via settings."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_pid_exists.return_value = False
        manager.launched_pid = 4242
        manager._exe_path = None

        with patch(
            "src.process_manager.settings.palworldServerExePath",
            "/home/gameserver/pwserver/pwserver",
        ):
            result = manager.terminate_process()

        assert result is True
        args, _kwargs = mock_run.call_args
        assert args[0] == ["/home/gameserver/pwserver/pwserver", "stop"]


class TestIsProcessRunningPidRecovery:
    """is_process_running() must self-heal if the tracked pid was replaced
    by a new one outside palworld-monitor's control (e.g. an LGSM/Steam
    update cron restarting the server without going through start/stop)."""

    @patch("src.process_manager.bus.publish")
    @patch("src.process_manager.psutil.Process")
    def test_recovers_when_tracked_pid_gone_and_replacement_found(
        self, mock_process_cls, mock_publish, manager
    ):
        manager.launched_pid = 111
        manager._save_pid_to_file(111)
        mock_process_cls.side_effect = psutil.NoSuchProcess(111)
        manager.find_process_pid = MagicMock(return_value=222)

        result = manager.is_process_running()

        assert result is True
        assert manager.launched_pid == 222
        with open(manager.pid_file_name(), "r") as f:
            assert f.read().strip() == "222"
        os.remove(manager.pid_file_name())
        # Adopting a replacement must be announced, or nothing starts
        # status polling for it and the app sits at "offline".
        mock_publish.assert_called_once_with(Event.SERVER_STARTED, {"pid": 222})

    @patch("src.process_manager.psutil.Process")
    def test_stays_offline_when_no_replacement_found(self, mock_process_cls, manager):
        manager.launched_pid = 111
        mock_process_cls.side_effect = psutil.NoSuchProcess(111)
        manager.find_process_pid = MagicMock(return_value=None)

        result = manager.is_process_running()

        assert result is False
        # Left unchanged so the next poll retries -- self-heals through a
        # transient gap where the old process died but a new one hasn't
        # started yet.
        assert manager.launched_pid == 111

    @patch("src.process_manager.psutil.Process")
    def test_does_not_re_detect_when_tracked_pid_still_alive(
        self, mock_process_cls, manager
    ):
        manager.launched_pid = 111
        mock_proc = MagicMock()
        mock_proc.is_running.return_value = True
        mock_process_cls.return_value = mock_proc
        manager.find_process_pid = MagicMock()

        result = manager.is_process_running()

        assert result is True
        assert manager.launched_pid == 111
        manager.find_process_pid.assert_not_called()
