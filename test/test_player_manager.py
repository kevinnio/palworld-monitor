"""
Tests for the PlayerManager module.
"""

import pytest
import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.player_manager import PlayerManager
from src.events import Event, EventBus


class TestPlayerManager:
    """Test suite for PlayerManager class."""

    @pytest.fixture
    def player_manager(self, tmp_path):
        """Create a PlayerManager that persists into a temporary directory."""
        return PlayerManager(EventBus(), data_file=str(tmp_path / "players.json"))

    def test_initialization(self, player_manager):
        """Test PlayerManager can track players after initialization."""
        # Test behavior: manager can track players
        player_manager.update_players_from_server(
            [["Player1", "uid1", "123456789", "10"]]
        )
        assert player_manager.get_player_count() == 1
        assert player_manager.get_total_player_count() == 1

    def test_update_players_from_server(self, player_manager):
        """Test updating players from server data."""
        current_players = [
            ["Player1", "uid1", "123456789", "10"],
            ["Player2", "uid2", "987654321", "15"],
        ]

        player_manager.update_players_from_server(current_players)

        assert "123456789" in player_manager.players
        assert "987654321" in player_manager.players
        assert player_manager.players["123456789"]["name"] == "Player1"
        assert player_manager.players["123456789"]["currently_online"] is True

    def test_update_players_marks_offline(self, player_manager):
        """Test that players not in current list are marked offline."""
        # First update with players online
        player_manager.update_players_from_server(
            [["Player1", "uid1", "123456789", "10"]]
        )
        assert player_manager.players["123456789"]["currently_online"] is True

        # Update with empty list
        player_manager.update_players_from_server([])
        assert player_manager.players["123456789"]["currently_online"] is False

    def test_get_player_count(self, player_manager):
        """Test getting count of online players."""
        player_manager.update_players_from_server(
            [["Player1", "uid1", "111", "10"], ["Player2", "uid2", "222", "15"]]
        )
        assert player_manager.get_player_count() == 2

        player_manager.update_players_from_server([])
        assert player_manager.get_player_count() == 0

    def test_get_total_player_count(self, player_manager):
        """Test getting total count of all players."""
        player_manager.update_players_from_server(
            [["Player1", "uid1", "111", "10"], ["Player2", "uid2", "222", "15"]]
        )
        assert player_manager.get_total_player_count() == 2

        # Mark one offline
        player_manager.update_players_from_server([["Player1", "uid1", "111", "10"]])
        assert player_manager.get_total_player_count() == 2  # Total should still be 2

    def test_get_all_players(self, player_manager):
        """Test getting all players."""
        player_manager.update_players_from_server(
            [["Player1", "uid1", "111", "10"], ["Player2", "uid2", "222", "15"]]
        )

        all_players = player_manager.get_all_players()
        assert len(all_players) == 2

    def test_get_online_players(self, player_manager):
        """Test getting only online players."""
        player_manager.update_players_from_server([["Player1", "uid1", "111", "10"]])
        # Mark one offline by updating with only the other player
        player_manager.update_players_from_server([["Player2", "uid2", "222", "15"]])

        online_players = player_manager.get_online_players()
        assert len(online_players) == 1
        assert online_players[0]["steam_id"] == "222"

    def test_get_offline_players(self, player_manager):
        """Test getting only offline players."""
        player_manager.update_players_from_server([["Player1", "uid1", "111", "10"]])
        # Mark offline by updating with empty list
        player_manager.update_players_from_server([])

        offline_players = player_manager.get_offline_players()
        assert len(offline_players) == 1
        assert offline_players[0]["steam_id"] == "111"

    def test_mark_all_offline_keeps_records(self, player_manager):
        """DOWN releases online players without forgetting who they were."""
        player_manager.update_players_from_server(
            [["Player1", "uid1", "111", "10"], ["Player2", "uid2", "222", "15"]]
        )

        player_manager.mark_all_offline()

        assert player_manager.get_player_count() == 0
        assert player_manager.get_total_player_count() == 2
        assert all(
            not player["currently_online"]
            for player in player_manager.get_all_players()
        )

        # Idempotent: a duplicate DOWN must not fail or duplicate events.
        player_manager.mark_all_offline()
        assert player_manager.get_total_player_count() == 2

    def test_save_and_load_player_data(self, player_manager):
        """Test saving and loading player data."""
        player_manager.update_players_from_server([["Player1", "uid1", "111", "10"]])

        # Create new manager instance to test loading
        new_manager = PlayerManager(EventBus(), data_file=player_manager.data_file)

        assert "111" in new_manager.players
        assert new_manager.players["111"]["name"] == "Player1"

    def test_update_existing_player(self, player_manager):
        """Test updating an existing player."""
        player_manager.update_players_from_server([["Player1", "uid1", "111", "5"]])
        player_manager.update_players_from_server(
            [["Player1Updated", "uid1", "111", "10"]]
        )

        player = player_manager.players["111"]
        assert player["name"] == "Player1Updated"
        assert player["level"] == "10"

    def test_player_last_seen_timestamp(self, player_manager):
        """Every roster sample refreshes the player's last-seen time."""
        import time

        player_manager.update_players_from_server([["TestPlayer", "uid1", "111", "10"]])
        first_seen = player_manager.players["111"]["last_online"]

        time.sleep(0.1)
        player_manager.update_players_from_server([["TestPlayer", "uid1", "111", "10"]])
        second_seen = player_manager.players["111"]["last_online"]

        assert second_seen > first_seen

    def test_tracking_disabled_drops_the_roster(self, player_manager, monkeypatch):
        """features.playerTracking: false — a roster sample is not stored or persisted."""
        monkeypatch.setattr("src.player_manager.settings.enablePlayerTracking", False)

        bus_events = []
        player_manager.bus.subscribe(
            Event.PLAYER_JOINED, lambda data: bus_events.append(data)
        )

        player_manager.update_players_from_server([["Player1", "uid1", "111", "10"]])

        assert player_manager.players == {}
        assert player_manager.get_total_player_count() == 0
        assert bus_events == []
        with open(player_manager.data_file, encoding="utf-8") as f:
            assert json.load(f)["players"] == {}

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading when data file doesn't exist."""
        manager = PlayerManager(
            EventBus(), data_file=str(tmp_path / "nonexistent.json")
        )
        assert manager.players == {}

    def test_migration_from_uid_to_steamid(self, tmp_path):
        """A version-0 UID-keyed file is re-keyed by Steam ID and rewritten."""
        old_data = {
            "version": 0,
            "players": {
                "old_uid_123": {
                    "name": "TestPlayer",
                    "uid": "old_uid_123",
                    "steam_id": "76561198000000001",
                    "currently_online": True,
                },
                "old_uid_456": {
                    "name": "NoSteamId",
                    "uid": "old_uid_456",
                },
                "old_uid_789": {
                    "name": "UnknownSteamId",
                    "uid": "old_uid_789",
                    "steam_id": "Unknown",
                },
            },
        }

        data_file = str(tmp_path / "players.json")
        with open(data_file, "w") as f:
            json.dump(old_data, f)

        manager = PlayerManager(EventBus(), data_file=data_file)

        # Entries without a usable Steam ID cannot be addressed by the REST API.
        assert list(manager.players) == ["76561198000000001"]
        assert manager.players["76561198000000001"]["name"] == "TestPlayer"
        assert "uid" not in manager.players["76561198000000001"]

        # The migrated file is persisted, so the next run skips migration.
        with open(data_file, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["version"] == 1
        assert list(saved["players"]) == ["76561198000000001"]
        assert PlayerManager(EventBus(), data_file=data_file).players == manager.players
