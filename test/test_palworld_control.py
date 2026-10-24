"""
Tests for the PalWorldController module.
"""

import pytest
from unittest.mock import patch
from src.palworld_control import PalWorldController
from test.support import create_mock_api_client
from src.events import Event


class TestPalWorldController:
    """Test suite for PalWorldController."""

    @pytest.fixture
    def mock_client(self):
        return create_mock_api_client()

    def test_init(
        self,
        mock_settings,
        mock_client,
        mock_process_manager,
        mock_player_manager,
        mock_banlist_manager,
    ):
        controller = PalWorldController(
            client=mock_client,
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
            banlist_manager=mock_banlist_manager,
        )

        assert controller.is_palworld_process_running() is False
        info = controller.current_server_info
        assert info["running"] is False
        assert info["playerCount"] == 0

    def test_is_palworld_process_running(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = True
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        assert controller.is_palworld_process_running() is True

    @patch("src.events.bus.publish")
    def test_start_server_success(
        self, mock_publish, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = False
        with (
            patch("os.path.exists", return_value=False),
            patch("time.time", return_value=1000),
            patch("src.palworld_control.settings") as mock_ctrl_settings,
        ):
            mock_ctrl_settings.palworldServerExePath = mock_settings.palworldServerExePath
            mock_ctrl_settings.palworldExeArguments = mock_settings.palworldExeArguments

            controller = PalWorldController(
                client=mock_client, process_manager=mock_process_manager
            )
            controller.last_server_started_time = 0
            result = controller.start_server()
            assert result is True
            mock_publish.assert_any_call(
                Event.CMD_START_SERVER,
                {
                    "exe_path": mock_settings.palworldServerExePath,
                    "exe_args": mock_settings.palworldExeArguments,
                },
            )

    def test_start_server_already_running(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = True
        with (
            patch("os.path.exists", return_value=False),
            patch("time.time", return_value=1000),
        ):
            controller = PalWorldController(
                client=mock_client, process_manager=mock_process_manager
            )
            result = controller.start_server()
            assert result is False

    @patch("src.events.bus.publish")
    def test_stop_server_success(
        self, mock_publish, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = True
        with (
            patch("os.path.exists", return_value=False),
            patch("time.time", return_value=1000),
            patch("threading.Thread"),
        ):
            controller = PalWorldController(
                client=mock_client, process_manager=mock_process_manager
            )
            controller.last_server_stopped_time = 0
            result = controller.stop_server()
            assert result is True
            mock_publish.assert_any_call(Event.CMD_STOP_SERVER, {})

    def test_stop_server_not_running(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = False
        with patch("os.path.exists", return_value=False), patch("threading.Thread"):
            controller = PalWorldController(
                client=mock_client, process_manager=mock_process_manager
            )
            controller.stop_server()
            assert controller.current_server_info["running"] is False

    def test_get_current_server_info(
        self, mock_settings, mock_client, mock_process_manager
    ):
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        controller.current_server_info = {
            "running": True,
            "playerCount": 2,
            "players": [["P1"], ["P2"]],
        }
        assert controller.current_server_info["running"] is True

    def test_server_status_event_updates_current_server_info(
        self, mock_settings, mock_client, mock_process_manager, mock_player_manager
    ):
        controller = PalWorldController(
            client=mock_client,
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
        )
        controller._on_server_status(
            {
                "running": True,
                "playerCount": 2,
                "players": [["P1", "p1", "u1", "10"], ["P2", "p2", "u2", "15"]],
                "banned_players": [],
            }
        )
        assert controller.current_server_info["running"] is True
        assert controller.current_server_info["playerCount"] == 2

    def test_server_status_event_triggers_auto_stop_with_zero_players(
        self, mock_settings, mock_client, mock_process_manager, mock_player_manager
    ):
        controller = PalWorldController(
            client=mock_client,
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
        )
        controller.last_server_started_time = 0
        with patch.object(controller, "_handle_auto_stop_condition") as mock_handle:
            controller._on_server_status(
                {
                    "running": True,
                    "playerCount": 0,
                    "players": [],
                    "banned_players": [],
                }
            )
            mock_handle.assert_called_once()

    @patch("src.events.bus.publish")
    def test_kick_player(
        self,
        mock_publish,
        mock_settings,
        mock_client,
        mock_process_manager,
        mock_player_manager,
    ):
        controller = PalWorldController(
            client=mock_client,
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
        )
        result = controller.kick_player("123")
        assert result is True
        mock_publish.assert_any_call(Event.CMD_KICK_PLAYER, {"steam_id": "123"})

    @patch("src.events.bus.publish")
    def test_ban_player(
        self,
        mock_publish,
        mock_settings,
        mock_client,
        mock_process_manager,
        mock_player_manager,
        mock_banlist_manager,
    ):
        controller = PalWorldController(
            client=mock_client,
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
            banlist_manager=mock_banlist_manager,
        )
        result = controller.ban_player("123")
        assert result is True
        mock_publish.assert_any_call(Event.CMD_BAN_PLAYER, {"steam_id": "123"})

    @patch("src.events.bus.publish")
    def test_unban_player(
        self,
        mock_publish,
        mock_settings,
        mock_client,
        mock_process_manager,
        mock_player_manager,
        mock_banlist_manager,
    ):
        controller = PalWorldController(
            client=mock_client,
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
            banlist_manager=mock_banlist_manager,
        )
        result = controller.unban_player("123")
        assert result is True
        mock_publish.assert_any_call(Event.CMD_UNBAN_PLAYER, {"steam_id": "123"})

    def test_get_server_status_is_running(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = True
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        assert controller.is_palworld_process_running() is True

    def test_stop_server_command_emitted(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = True
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        controller.last_server_stopped_time = 0
        with patch("src.events.bus.publish") as mock_publish:
            result = controller.stop_server()
            assert result is True
            mock_publish.assert_any_call(Event.CMD_STOP_SERVER, {})

    def test_server_info_attribute_access(
        self, mock_settings, mock_client, mock_process_manager
    ):
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        assert "running" in controller.current_server_info
        assert "playerCount" in controller.current_server_info
        assert "players" in controller.current_server_info

    def test_on_server_started_starts_update_thread(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = True
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        with patch.object(controller, "start_server_info_update_thread") as mock_start:
            controller._on_server_started({"pid": 1234})
            mock_start.assert_called_once()

    def test_on_server_stopped_stops_update_thread(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = False
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        with patch.object(controller, "stop_server_info_update_thread") as mock_stop:
            controller._on_server_stopped({"pid": 1234})
            mock_stop.assert_called_once()

    def test_get_players_for_web_returns_player_manager_data(
        self, mock_settings, mock_client, mock_process_manager, mock_player_manager
    ):
        mock_player_manager.get_all_players.return_value = [
            {
                "name": "Player1",
                "steam_id": "123",
                "level": "10",
                "currently_online": True,
            }
        ]
        controller = PalWorldController(
            client=mock_client,
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
        )
        players = controller.get_players_for_web()
        assert players == mock_player_manager.get_all_players.return_value

    def _controller_without_loop(
        self, mock_process_manager, mock_client, mock_player_manager=None
    ):
        """Controller without its background loop, for deterministic tests."""
        kwargs = {"client": mock_client, "process_manager": mock_process_manager}
        if mock_player_manager is not None:
            kwargs["player_manager"] = mock_player_manager
        with patch.object(PalWorldController, "_start_detection_loop"):
            return PalWorldController(**kwargs)

    @patch("src.events.bus.publish")
    def test_process_gone_emits_server_stopped(
        self, mock_publish, mock_settings, mock_client, mock_process_manager
    ):
        """Regression for issue #6: a tracked process exiting outside the
        monitor (crash, closed window) must fire SERVER_STOPPED so the status
        poll loop halts and the auto-start listener re-arms."""
        mock_process_manager.launched_pid = 1234
        mock_process_manager.is_process_running.return_value = False
        controller = self._controller_without_loop(mock_process_manager, mock_client)
        controller._check_tracked_process_gone()
        mock_publish.assert_called_once_with(Event.SERVER_STOPPED, {"pid": None})
        mock_process_manager.clear_pid.assert_called_once()

    @patch("src.events.bus.publish")
    def test_process_reacquired_emits_no_stopped_event(
        self, mock_publish, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.launched_pid = 1234
        mock_process_manager.is_process_running.return_value = True
        controller = self._controller_without_loop(mock_process_manager, mock_client)
        controller._check_tracked_process_gone()
        mock_publish.assert_not_called()
        mock_process_manager.clear_pid.assert_not_called()

    @patch("src.events.bus.publish")
    def test_no_tracked_process_emits_no_stopped_event(
        self, mock_publish, mock_settings, mock_client, mock_process_manager
    ):
        controller = self._controller_without_loop(mock_process_manager, mock_client)
        controller._check_tracked_process_gone()
        mock_publish.assert_not_called()
        mock_process_manager.clear_pid.assert_not_called()

    def test_detection_loop_keeps_watching_after_start(
        self, mock_settings, mock_client, mock_process_manager
    ):
        """The loop must not exit once a process is tracked: it is also the
        watcher that notices the process disappearing."""
        mock_process_manager.launched_pid = 1234
        mock_process_manager.is_process_running.return_value = True
        controller = PalWorldController(
            client=mock_client, process_manager=mock_process_manager
        )
        calls = []

        def stop_after_two_calls():
            calls.append(1)
            if len(calls) >= 2:
                controller._detection_stop_event.set()

        with (
            patch.object(
                controller,
                "_detect_existing_server_process",
                side_effect=stop_after_two_calls,
            ),
            patch.object(controller, "_check_tracked_process_gone"),
        ):
            controller._server_detection_loop()
        assert len(calls) >= 2

    def test_down_server_marks_tracked_players_offline(
        self, mock_settings, mock_client, mock_process_manager, mock_player_manager
    ):
        """Ghost players: a server that stops must release its tracked
        players, otherwise they show as online forever."""
        mock_process_manager.is_process_running.return_value = False
        controller = self._controller_without_loop(
            mock_process_manager, mock_client, mock_player_manager
        )
        controller._update_server_info_with_players()
        mock_client.get_player_names.assert_not_called()
        mock_player_manager.update_players_from_server.assert_called_once_with([])

    def test_up_server_updates_tracked_players(
        self, mock_settings, mock_client, mock_process_manager, mock_player_manager
    ):
        mock_process_manager.is_process_running.return_value = True
        mock_client.get_player_names.return_value = [["P1", "p1", "u1", "10"]]
        controller = self._controller_without_loop(
            mock_process_manager, mock_client, mock_player_manager
        )
        controller._update_server_info_with_players()
        mock_player_manager.update_players_from_server.assert_called_once_with(
            [["P1", "p1", "u1", "10"]]
        )

    def test_server_stopped_releases_tracked_players(
        self, mock_settings, mock_client, mock_process_manager, mock_player_manager
    ):
        """Deterministic ghost-player release on the stop event, regardless
        of poll phase."""
        mock_process_manager.is_process_running.return_value = False
        controller = self._controller_without_loop(
            mock_process_manager, mock_client, mock_player_manager
        )
        controller._on_server_stopped({})
        mock_player_manager.update_players_from_server.assert_called_once_with([])

    def test_auto_stop_still_arms_when_running_with_no_players(
        self, mock_settings, mock_client, mock_process_manager
    ):
        """Guard m1: the not-running skip must not disable auto-stop."""
        mock_process_manager.is_process_running.return_value = True
        controller = self._controller_without_loop(mock_process_manager, mock_client)
        controller.current_server_info["running"] = True
        controller.last_server_started_time = 0
        with patch("threading.Thread"):
            controller._handle_auto_stop_condition()
        assert controller.auto_stop_delay_thread is not None

    def test_auto_stop_skipped_when_server_not_running(
        self, mock_settings, mock_client, mock_process_manager
    ):
        mock_process_manager.is_process_running.return_value = False
        controller = self._controller_without_loop(mock_process_manager, mock_client)
        controller._handle_auto_stop_condition()
        assert controller.auto_stop_delay_thread is None
