"""
Tests for LGSMProcessManager and process identity tracking.

These are unit tests that mock subprocess/psutil calls rather than
launching a real LGSM script.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
import psutil

from src.lifecycle.messages import ProcessIdentity
from src.process_manager import LGSMProcessManager


@pytest.fixture
def manager():
    m = LGSMProcessManager()
    # Keep the tests fast: real timeouts default to 30s+.
    m.STARTUP_TIMEOUT = 0.2
    m.STARTUP_POLL_INTERVAL = 0.01
    return m


class TestLGSMProcessManager:
    def test_pid_file_name(self, manager):
        assert manager.pid_file_name() == "palworld_server.lgsm.pid"

    @patch("src.process_manager.subprocess.run")
    def test_launch_process_records_identity_before_returning(self, mock_run, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        identity = ProcessIdentity(4242, 1.0)
        manager.find_process_identity = MagicMock(return_value=identity)

        result = manager.launch_process("/home/gameserver/pwserver/pwserver", "")

        mock_run.assert_called_once()
        args, _kwargs = mock_run.call_args
        assert args[0] == ["/home/gameserver/pwserver/pwserver", "start"]
        assert result == identity
        assert manager.tracked_identity == identity
        assert manager._exe_path == "/home/gameserver/pwserver/pwserver"

        assert os.path.exists(manager.pid_file_name())
        with open(manager.pid_file_name(), "r") as f:
            assert f.read().strip() == "4242"

    @patch("src.process_manager.subprocess.run")
    def test_launch_process_start_command_fails(self, mock_run, manager):
        mock_run.side_effect = Exception("script not found")
        manager.find_process_identity = MagicMock(return_value=ProcessIdentity(4242))

        assert manager.launch_process("/does/not/exist", "") is None
        assert manager.tracked_identity is None

    @patch("src.process_manager.subprocess.run")
    def test_launch_process_no_pid_found(self, mock_run, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        manager.find_process_identity = MagicMock(return_value=None)

        assert manager.launch_process("/home/gameserver/pwserver/pwserver", "") is None
        assert manager.tracked_identity is None

    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_success(self, mock_run, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        identity = ProcessIdentity(4242, 1.0)
        manager.set_known_identity(identity)
        manager._exe_path = "/home/gameserver/pwserver/pwserver"
        manager.identity_is_running = MagicMock(return_value=False)

        result = manager.terminate_process(identity)

        assert result is True
        assert manager.tracked_identity is None
        assert not os.path.exists(manager.pid_file_name())
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["/home/gameserver/pwserver/pwserver", "stop"]
        assert kwargs["timeout"] == manager.LGSM_COMMAND_TIMEOUT

    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_stop_command_fails_leaves_tracking_for_detection(
        self, mock_run, manager
    ):
        """A failed/timed-out LGSM invocation doesn't necessarily mean the
        server isn't shutting down -- we still watch the identity rather than
        giving up outright."""
        mock_run.side_effect = Exception("boom")
        identity = ProcessIdentity(4242, 1.0)
        manager.set_known_identity(identity)
        manager._exe_path = "/home/gameserver/pwserver/pwserver"
        manager.identity_is_running = MagicMock(return_value=True)

        result = manager.terminate_process(identity)

        assert result is None
        # Not resolved synchronously. Detection still needs the tracked identity.
        assert manager.tracked_identity == identity

    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_still_running_after_command(self, mock_run, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        identity = ProcessIdentity(4242, 1.0)
        manager.set_known_identity(identity)
        manager._exe_path = "/home/gameserver/pwserver/pwserver"
        manager.identity_is_running = MagicMock(return_value=True)

        result = manager.terminate_process(identity)

        assert result is None
        assert manager.tracked_identity == identity

    @patch("src.process_manager.subprocess.run")
    def test_pending_shutdown_is_observed_without_clearing_tracking(
        self, mock_run, manager
    ):
        """Detection, not a second watcher, reports a delayed exit."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        identity = ProcessIdentity(4242, 1.0)
        manager.set_known_identity(identity)
        manager._exe_path = "/home/gameserver/pwserver/pwserver"
        manager.identity_is_running = MagicMock(return_value=True)
        manager.find_process_identity = MagicMock(return_value=None)

        result = manager.terminate_process(identity)

        assert result is None
        assert manager.tracked_identity == identity  # not resolved synchronously

        manager.identity_is_running.return_value = False
        snapshot = manager.observe()

        assert snapshot.tracked == identity
        assert snapshot.tracked_running is False
        assert manager.tracked_identity == identity
        assert os.path.exists(manager.pid_file_name())

    def test_terminate_process_with_no_pid(self, manager):
        assert manager.terminate_process() is False

    @patch("src.process_manager.subprocess.run")
    def test_terminate_process_falls_back_to_settings_exe_path(self, mock_run, manager):
        """If the manager never launched the server itself in this process
        (e.g. monitor restarted and re-attached to an already-running LGSM
        server), it should still know how to stop it via settings."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        identity = ProcessIdentity(4242, 1.0)
        manager.set_known_identity(identity)
        manager._exe_path = None
        manager.identity_is_running = MagicMock(return_value=False)

        with patch(
            "src.process_manager.settings.palworldServerExePath",
            "/home/gameserver/pwserver/pwserver",
        ):
            result = manager.terminate_process(identity)

        assert result is True
        args, _kwargs = mock_run.call_args
        assert args[0] == ["/home/gameserver/pwserver/pwserver", "stop"]

    @patch("src.process_manager.subprocess.run")
    def test_wrong_expected_identity_does_not_stop_a_replacement(
        self, mock_run, manager
    ):
        replacement = ProcessIdentity(222, 2.0)
        manager.set_known_identity(replacement)

        assert manager.terminate_process(ProcessIdentity(111, 1.0)) is False
        assert manager.tracked_identity == replacement
        mock_run.assert_not_called()


class TestProcessIdentityTracking:
    @patch("src.process_manager.psutil.Process")
    def test_zombie_identity_is_not_running(self, process, manager):
        process.return_value.status.return_value = psutil.STATUS_ZOMBIE
        process.return_value.is_running.return_value = True

        assert manager.identity_is_running(ProcessIdentity(4242)) is False

    @patch("src.process_manager.psutil.process_iter")
    def test_scan_ignores_zombies_and_finds_a_live_replacement(
        self, processes, manager
    ):
        zombie = MagicMock()
        zombie.info = {
            "pid": 111,
            "name": "PalServer",
            "status": psutil.STATUS_ZOMBIE,
            "create_time": 1.0,
        }
        live = MagicMock()
        live.info = {
            "pid": 222,
            "name": "PalServer",
            "status": psutil.STATUS_RUNNING,
            "create_time": 2.0,
        }
        processes.return_value = [zombie, live]

        assert manager.find_process_identity("PalServer") == ProcessIdentity(222, 2.0)

    def test_clear_identity_is_compare_and_clear(self, manager):
        original = ProcessIdentity(111, 1.0)
        replacement = ProcessIdentity(222, 2.0)
        manager.set_known_identity(original)
        manager.set_known_identity(replacement)

        assert manager.clear_identity(original) is False
        assert manager.tracked_identity == replacement
        assert manager.clear_identity(replacement) is True
        assert manager.tracked_identity is None

    def test_observe_reports_replacement_without_mutating_tracking(self, manager):
        tracked = ProcessIdentity(111, 1.0)
        replacement = ProcessIdentity(222, 2.0)
        manager.set_known_identity(tracked)
        manager.identity_is_running = MagicMock(return_value=False)
        manager.find_process_identity = MagicMock(return_value=replacement)

        snapshot = manager.observe()

        assert snapshot.tracked == tracked
        assert snapshot.tracked_running is False
        assert snapshot.found == replacement
        assert manager.tracked_identity == tracked

    def test_loads_identity_from_pid_file(self, manager):
        pid = os.getpid()
        manager._save_pid_to_file(pid)

        reloaded = LGSMProcessManager()

        assert reloaded.tracked_identity is not None
        assert reloaded.tracked_identity.pid == pid
        assert reloaded.tracked_identity.create_time is not None
