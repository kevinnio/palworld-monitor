"""
Tests for the BanlistManager module.
"""

import pytest
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.banlist_manager import BanlistManager
from test.support import (
    create_temp_file_with_content,
    cleanup_temp_file,
)


class TestBanlistManager:
    """Test suite for BanlistManager class."""

    @pytest.mark.parametrize(
        "exe_dir", [".", "Pal/Binaries/Win64", "Pal/Binaries/Linux"]
    )
    @pytest.mark.parametrize("exists", [False, True])
    def test_detects_native_banlist(self, tmp_path, mock_settings, exe_dir, exists):
        binaries = tmp_path / "Pal/Binaries"
        binaries.mkdir(parents=True)
        native = tmp_path / "Pal/Saved/SaveGames/banlist.txt"
        if exists:
            native.parent.mkdir(parents=True)
            native.write_text("steam_123,UID123\n")

        mock_settings.palworldServerExePath = str(tmp_path / exe_dir / "PalServer.exe")
        manager = BanlistManager()

        assert manager.banlist_path == str(native)
        assert manager.get_banned_players() == (["steam_123"] if exists else [])

    def test_native_pairs_are_unique_ids(self, tmp_path):
        path = tmp_path / "banlist.txt"
        path.write_text("# Bans\nsteam_123,UID123\nsteam_123\n steam_456,UID456\n")
        manager = BanlistManager(str(path))

        assert manager.get_banned_players() == ["steam_123", "steam_456"]
        assert manager.is_banned("steam_123") is True

    def test_native_layout_does_not_use_stale_legacy_banlist(
        self, tmp_path, mock_settings
    ):
        binaries = tmp_path / "Pal/Binaries/Win64"
        binaries.mkdir(parents=True)
        (binaries / "banlist.txt").write_text("steam_stale\n")
        mock_settings.palworldServerExePath = str(binaries / "PalServer.exe")

        manager = BanlistManager()

        assert manager.banlist_path == str(tmp_path / "Pal/Saved/SaveGames/banlist.txt")

    @pytest.mark.parametrize("steam_id", ["", "steam_123,UID", "steam_123\nsteam_456"])
    def test_invalid_id_does_not_change_file(self, tmp_path, steam_id):
        path = tmp_path / "banlist.txt"
        path.write_text("steam_123,UID123\n")
        manager = BanlistManager(str(path))

        assert manager.add_ban(steam_id) is False
        assert manager.remove_ban(steam_id) is False
        assert path.read_text() == "steam_123,UID123\n"

    def test_add_preserves_native_entries_and_comments(self, tmp_path):
        path = tmp_path / "banlist.txt"
        original = "# Bans\nsteam_123,UID123"
        path.write_text(original)
        manager = BanlistManager(str(path))

        assert manager.add_ban("steam_123") is True
        assert path.read_text() == original
        assert manager.add_ban("steam_456") is True
        assert path.read_text() == original + "\nsteam_456\n"

    def test_remove_preserves_other_native_entries(self, tmp_path):
        path = tmp_path / "banlist.txt"
        path.write_text("# Bans\nsteam_123,UID123\nsteam_123\n\nsteam_456,UID456\n")
        manager = BanlistManager(str(path))

        assert manager.remove_ban("steam_123") is True
        assert path.read_text() == "# Bans\n\nsteam_456,UID456\n"

    @pytest.mark.parametrize("action", ["add_ban", "remove_ban"])
    def test_read_failure_does_not_overwrite_bans(self, tmp_path, action):
        path = tmp_path / "banlist.txt"
        path.write_text("steam_123,UID123\n")
        manager = BanlistManager(str(path))

        with patch.object(
            manager, "_read_lines", side_effect=PermissionError("read denied")
        ):
            assert getattr(manager, action)("steam_123") is False

        assert path.read_text() == "steam_123,UID123\n"

    def test_failed_replace_leaves_original_and_no_temp_file(self, tmp_path):
        path = tmp_path / "banlist.txt"
        path.write_text("steam_123,UID123\nsteam_456,UID456\n")
        manager = BanlistManager(str(path))

        with patch(
            "src.banlist_manager.os.replace",
            side_effect=PermissionError("write denied"),
        ):
            assert manager.remove_ban("steam_123") is False

        assert path.read_text() == "steam_123,UID123\nsteam_456,UID456\n"
        assert list(tmp_path.iterdir()) == [path]

    def test_notification_does_not_hold_file_lock(self, tmp_path):
        import threading
        from src.events import bus, Event

        manager = BanlistManager(str(tmp_path / "banlist.txt"))
        read_done = threading.Event()
        observed = []

        def subscriber(data):
            worker = threading.Thread(
                target=lambda: (manager.get_banned_players(), read_done.set())
            )
            worker.start()
            observed.append(read_done.wait(1))
            worker.join(timeout=1)

        bus.subscribe(Event.BAN_ADDED, subscriber)
        assert manager.add_ban("steam_123") is True
        assert observed == [True]

    @pytest.fixture
    def temp_banlist_file(self):
        """Create a temporary banlist file."""
        temp_file_path = create_temp_file_with_content(
            "SteamID1\nSteamID2\n", suffix=".txt"
        )
        yield temp_file_path
        cleanup_temp_file(temp_file_path)

    @pytest.fixture
    def banlist_manager(self, temp_banlist_file):
        """Create a BanlistManager instance with temporary banlist file."""
        return BanlistManager(banlist_path=temp_banlist_file)

    def test_initialization_with_path(self, temp_banlist_file):
        """Test initialization with explicit banlist path."""
        manager = BanlistManager(banlist_path=temp_banlist_file)
        assert manager.banlist_path == temp_banlist_file

    def test_initialization_without_path(self, temp_dir):
        """Test initialization without explicit path can read and write banlist."""
        # Use a temporary directory for the server executable path
        temp_server_exe = os.path.join(temp_dir, "PalServer.exe")

        with patch("src.banlist_manager.settings") as mock_settings:
            mock_settings.palworldServerExePath = temp_server_exe
            manager = BanlistManager()
            # Test behavior: manager can read banlist (even if empty)
            banned_ids = manager.get_banned_players()
            assert isinstance(banned_ids, list)
            # Test behavior: manager can add bans
            result = manager.add_ban("TestSteamID")
            assert result is True
            assert manager.is_banned("TestSteamID") is True

    def test_get_banned_players(self, banlist_manager):
        """Test reading banlist from file."""
        banned_ids = banlist_manager.get_banned_players()
        assert "SteamID1" in banned_ids
        assert "SteamID2" in banned_ids
        assert len(banned_ids) == 2

    def test_get_banned_players_empty_file(self):
        """Test reading from empty banlist file."""
        temp_file_path = create_temp_file_with_content("", suffix=".txt")

        try:
            manager = BanlistManager(banlist_path=temp_file_path)
            banned_ids = manager.get_banned_players()
            assert banned_ids == []
        finally:
            cleanup_temp_file(temp_file_path)

    def test_get_banned_players_nonexistent_file(self):
        """Test reading from nonexistent banlist file."""
        manager = BanlistManager(banlist_path="/nonexistent/path/banlist.txt")
        banned_ids = manager.get_banned_players()
        assert banned_ids == []

    def test_add_ban(self, banlist_manager):
        """Test adding a Steam ID to banlist."""
        initial_ids = banlist_manager.get_banned_players()
        result = banlist_manager.add_ban("SteamID3")

        assert result is True
        new_ids = banlist_manager.get_banned_players()
        assert "SteamID3" in new_ids
        assert len(new_ids) == len(initial_ids) + 1

    def test_add_ban_duplicate(self, banlist_manager):
        """Test adding duplicate Steam ID (should return True but not create duplicates)."""
        result = banlist_manager.add_ban("SteamID1")  # Already exists
        assert result is True

        banned_ids = banlist_manager.get_banned_players()
        # Count occurrences
        count = banned_ids.count("SteamID1")
        assert count == 1

    def test_remove_ban(self, banlist_manager):
        """Test removing a Steam ID from banlist."""
        assert "SteamID1" in banlist_manager.get_banned_players()

        result = banlist_manager.remove_ban("SteamID1")
        assert result is True

        banned_ids = banlist_manager.get_banned_players()
        assert "SteamID1" not in banned_ids
        assert "SteamID2" in banned_ids  # Other IDs should remain

    def test_remove_ban_nonexistent(self, banlist_manager):
        """Test removing non-existent Steam ID (should return True)."""
        initial_ids = banlist_manager.get_banned_players()
        result = banlist_manager.remove_ban("NonexistentID")

        assert result is True
        # Should remain unchanged
        assert banlist_manager.get_banned_players() == initial_ids

    def test_is_banned(self, banlist_manager):
        """Test checking if a Steam ID is banned."""
        assert banlist_manager.is_banned("SteamID1") is True
        assert banlist_manager.is_banned("SteamID2") is True
        assert banlist_manager.is_banned("SteamID999") is False

    def test_banlist_strips_whitespace(self):
        """Test that banlist reading strips whitespace."""
        temp_file_path = create_temp_file_with_content(
            "  SteamID1  \nSteamID2\n  SteamID3  ", suffix=".txt"
        )

        try:
            manager = BanlistManager(banlist_path=temp_file_path)
            banned_ids = manager.get_banned_players()
            assert "SteamID1" in banned_ids
            assert "SteamID2" in banned_ids
            assert "SteamID3" in banned_ids
            # Should not have IDs with spaces
            assert "  SteamID1  " not in banned_ids
        finally:
            cleanup_temp_file(temp_file_path)

    def test_banlist_ignores_empty_lines(self):
        """Test that banlist reading ignores empty lines."""
        temp_file_path = create_temp_file_with_content(
            "SteamID1\n\nSteamID2\n\n\nSteamID3", suffix=".txt"
        )

        try:
            manager = BanlistManager(banlist_path=temp_file_path)
            banned_ids = manager.get_banned_players()
            assert len(banned_ids) == 3
            assert "" not in banned_ids
        finally:
            cleanup_temp_file(temp_file_path)

    def test_add_ban_creates_file(self):
        """Test that adding ban creates file if it doesn't exist."""
        import tempfile

        temp_file = tempfile.mktemp(suffix=".txt")

        try:
            manager = BanlistManager(banlist_path=temp_file)
            result = manager.add_ban("SteamID1")

            assert result is True
            assert os.path.exists(temp_file)
            written_ids = manager.get_banned_players()
            assert "SteamID1" in written_ids
        finally:
            cleanup_temp_file(temp_file)

    def test_banlist_ignores_comments(self):
        """Test that banlist reading ignores comment lines."""
        temp_file_path = create_temp_file_with_content(
            "# This is a comment\nSteamID1\n# Another comment\nSteamID2", suffix=".txt"
        )

        try:
            manager = BanlistManager(banlist_path=temp_file_path)
            banned_ids = manager.get_banned_players()
            assert len(banned_ids) == 2
            assert "SteamID1" in banned_ids
            assert "SteamID2" in banned_ids
            assert "# This is a comment" not in banned_ids
        finally:
            cleanup_temp_file(temp_file_path)
