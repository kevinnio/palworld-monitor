"""
Extended tests for WebServer route handlers and event integration.
"""

from unittest.mock import MagicMock, patch
from src.web_server import WebServer

class TestWebServerEventHandlers:
    """Test suite for event handler methods."""

    def test_on_server_started_updates_cache(self, mock_settings):
        """Test that SERVER_STARTED event updates running state."""
        server = WebServer.__new__(WebServer)
        server.palworld_controller = MagicMock()
        server.state_cache = {
            "running": False,
            "playerCount": 0,
            "players": [],
            "banned_players": [],
        }
        server._lock = MagicMock()

        server._on_server_started({"pid": 12345})
        assert server.state_cache["running"] is True

    def test_on_server_stopped_clears_player_count_but_keeps_player_list(
        self, mock_settings
    ):
        """SERVER_STOPPED should reset the live player count to 0, but keep
        showing the last-known players (correctly marked offline via
        PlayerManager) instead of wiping to "No players found"."""
        server = WebServer.__new__(WebServer)
        server.palworld_controller = MagicMock()
        last_known_players = [
            {"name": "P1", "steam_id": "u1", "level": "10", "currently_online": False},
            {"name": "P2", "steam_id": "u2", "level": "15", "currently_online": False},
        ]
        server.palworld_controller.get_players_for_web.return_value = last_known_players
        server.state_cache = {
            "running": True,
            "playerCount": 2,
            "players": [["P1", "p1", "u1", "10"], ["P2", "p2", "u2", "15"]],
            "banned_players": [],
        }
        server._lock = MagicMock()
        server._sse_lock = MagicMock()
        server._sse_clients = []

        with patch.object(server, "_broadcast_sse") as mock_broadcast:
            server._on_server_stopped({"pid": 12345})
        assert server.state_cache["running"] is False
        assert server.state_cache["players"] == last_known_players
        assert server.state_cache["playerCount"] == 0
        # The poll thread that used to push status stops with this event,
        # so the stop itself must reach connected dashboards.
        mock_broadcast.assert_called_once()
        assert mock_broadcast.call_args[0][0]["data"]["running"] is False

    def test_on_server_status_updates_all_fields(self, mock_settings):
        """Test that SERVER_STATUS event updates all cached fields.

        Player data must come from the controller's processed records
        (dicts with name/level/steam_id), not the raw REST tuples carried
        on the event — the frontend can't render positional lists.
        """
        server = WebServer.__new__(WebServer)
        server.palworld_controller = MagicMock()
        processed_players = [
            {"name": "P1", "steam_id": "u1", "level": "10", "currently_online": True},
            {"name": "P2", "steam_id": "u2", "level": "15", "currently_online": True},
            {"name": "P3", "steam_id": "u3", "level": "20", "currently_online": True},
        ]
        server.palworld_controller.get_players_for_web.return_value = processed_players
        server.palworld_controller.player_manager.get_online_players.return_value = (
            processed_players
        )
        server.state_cache = {
            "running": False,
            "playerCount": 0,
            "players": [],
            "banned_players": [],
        }
        server._lock = MagicMock()
        server._sse_lock = MagicMock()
        server._sse_clients = []

        server._on_server_status(
            {
                "running": True,
                "playerCount": 3,
                "players": [["P1", "p1", "u1", "10"], ["P2", "p2", "u2", "15"], ["P3", "p3", "u3", "20"]],
                "banned_players": ["SteamID1"],
            }
        )

        assert server.state_cache["running"] is True
        assert server.state_cache["playerCount"] == 3
        assert server.state_cache["players"] == processed_players
        assert server.state_cache["players"][0]["name"] == "P1"
        assert server.state_cache["players"][0]["level"] == "10"
        assert "SteamID1" in server.state_cache["banned_players"]

    def test_sync_running_state_sets_cache_from_controller(self, mock_settings):
        """Test that _sync_running_state updates cache from controller."""
        server = WebServer.__new__(WebServer)
        server.palworld_controller = MagicMock()
        server.palworld_controller.is_palworld_process_running.return_value = True
        server.state_cache = {
            "running": False,
            "playerCount": 0,
            "players": [],
            "banned_players": [],
        }
        server._lock = MagicMock()

        server._sync_running_state()
        assert server.state_cache["running"] is True

    def test_sync_banned_players_loads_existing_bans(self, mock_settings):
        """Test that _sync_banned_players loads existing bans."""
        server = WebServer.__new__(WebServer)
        server.palworld_controller = MagicMock()
        server.palworld_controller.get_banned_players.return_value = ["SteamID1", "SteamID2"]
        server.state_cache = {
            "running": False,
            "playerCount": 0,
            "players": [],
            "banned_players": [],
        }
        server._lock = MagicMock()

        server._sync_banned_players()
        assert server.state_cache["banned_players"] == ["SteamID1", "SteamID2"]

    def test_sync_players_loads_existing_players_from_controller(self, mock_settings):
        """Test that _sync_players populates the cache from PlayerManager on
        startup, so a page load doesn't show "No players found" just
        because no SERVER_STATUS/SERVER_STOPPED event has fired yet in this
        monitor session (e.g. the server was already offline at startup)."""
        server = WebServer.__new__(WebServer)
        server.palworld_controller = MagicMock()
        known_players = [
            {"name": "P1", "steam_id": "u1", "level": "10", "currently_online": False},
        ]
        server.palworld_controller.get_players_for_web.return_value = known_players
        server.palworld_controller.player_manager.get_online_players.return_value = []
        server.state_cache = {
            "running": False,
            "playerCount": 0,
            "players": [],
            "banned_players": [],
        }
        server._lock = MagicMock()

        server._sync_players()
        assert server.state_cache["players"] == known_players
        assert server.state_cache["playerCount"] == 0


