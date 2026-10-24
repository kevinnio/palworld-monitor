"""
Tests for the PlayerManager module.
"""

import pytest
import sys
import os
import json
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.player_manager import PlayerManager
from src.events import EventBus
from test.support import (
    create_temp_data_dir,
    cleanup_temp_dir,
    create_mock_path_join,
)


class TestPlayerManager:
    """Test suite for PlayerManager class."""

    @pytest.fixture
    def temp_data_dir(self):
        """Create a temporary data directory for test files."""
        temp_dir, data_dir = create_temp_data_dir()
        yield data_dir
        cleanup_temp_dir(temp_dir)

    @pytest.fixture
    def player_manager(self, temp_data_dir, monkeypatch):
        """Create a PlayerManager instance with temporary data directory."""
        data_file_path = os.path.join(temp_data_dir, "players.json")
        mock_join = create_mock_path_join(data_file_path)

        monkeypatch.setattr("os.path.join", mock_join)
        manager = PlayerManager(EventBus())
        # Also set it directly to be sure
        manager.data_file = data_file_path
        return manager

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

    def test_save_and_load_player_data(self, player_manager, temp_data_dir):
        """Test saving and loading player data."""
        player_manager.update_players_from_server([["Player1", "uid1", "111", "10"]])
        player_manager._save_player_data()

        # Create new manager instance to test loading
        data_file_path = os.path.join(temp_data_dir, "players.json")
        mock_join = create_mock_path_join(data_file_path)

        with patch("os.path.join", mock_join):
            new_manager = PlayerManager(EventBus())
            new_manager.data_file = data_file_path
            new_manager._load_player_data()

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
        """Test that last_online timestamp is updated."""
        import time

        player_manager.update_players_from_server([["TestPlayer", "uid1", "111", "10"]])
        first_seen = player_manager.players["111"]["last_online"]

        time.sleep(0.1)
        player_manager.update_players_from_server([["TestPlayer", "uid1", "111", "10"]])
        second_seen = player_manager.players["111"]["last_online"]

        assert second_seen >= first_seen

    def test_load_nonexistent_file(self, temp_data_dir, monkeypatch):
        """Test loading when data file doesn't exist."""
        data_file_path = os.path.join(temp_data_dir, "nonexistent.json")
        mock_join = create_mock_path_join(data_file_path)

        monkeypatch.setattr("os.path.join", mock_join)
        manager = PlayerManager(EventBus())
        manager.data_file = data_file_path
        assert manager.players == {}

    def test_migration_from_uid_to_steamid(self, temp_data_dir, monkeypatch):
        """Test migration from old UID-based format to Steam ID format."""
        # Create old format data
        old_data = {
            "version": 0,
            "players": {
                "old_uid_123": {
                    "name": "TestPlayer",
                    "uid": "old_uid_123",
                    "is_online": True,
                }
            },
        }

        data_file = os.path.join(temp_data_dir, "players.json")
        with open(data_file, "w") as f:
            json.dump(old_data, f)

        mock_join = create_mock_path_join(data_file)
        monkeypatch.setattr("os.path.join", mock_join)
        manager = PlayerManager(EventBus())
        manager.data_file = data_file

        # Should have migrated and saved
        assert manager.version == 1
        # Note: Actual migration logic depends on implementation details
