from typing import List
import os
import logging
import threading
import tempfile
import getpass
from src.settings import settings
from src.events import EventBus, Event


class BanlistManager:
    """Manages the Palworld server banlist.txt file."""

    def __init__(self, bus: EventBus, banlist_path=None):
        """Initialize the banlist manager.

        Args:
            bus: Notification bus for BAN_ADDED/BAN_REMOVED.
            banlist_path: Optional path to banlist.txt. If None, will try to auto-detect.
        """
        self.banlist_path = banlist_path or self._detect_banlist_path()
        self._lock = threading.RLock()
        self.bus = bus
        self._warn_if_unusable()

    def _warn_if_unusable(self):
        """Log access problems when the player-tracking dashboard is enabled."""
        if not settings.useWebServer or not settings.enablePlayerTracking:
            return
        for warning in self.access_warnings():
            logging.warning(warning)

    def _detect_banlist_path(self):
        """Detect the banlist.txt file path based on server executable path.

        Returns:
            str or None: Path to banlist.txt or None if not found
        """
        try:
            server_exe_path = settings.palworldServerExePath
            if not server_exe_path:
                return None
            server_dir = os.path.dirname(os.path.abspath(server_exe_path))
            # LGSM keeps the game files in `serverfiles/` next to its script.
            possible_paths = [
                os.path.join(server_dir, "Pal", "Saved", "SaveGames", "banlist.txt"),
                os.path.join(
                    server_dir, "serverfiles", "Pal", "Saved", "SaveGames", "banlist.txt"
                ),
                os.path.join(
                    server_dir, "Pal", "Saved", "SaveGames", "0", "banlist.txt"
                ),
                os.path.join(server_dir, "Pal", "Saved", "banlist.txt"),
                os.path.join(server_dir, "banlist.txt"),
            ]
            root = server_dir
            for _ in range(4):
                for base in (root, os.path.join(root, "serverfiles")):
                    if os.path.isdir(os.path.join(base, "Pal", "Binaries")):
                        native_path = os.path.join(
                            base, "Pal", "Saved", "SaveGames", "banlist.txt"
                        )
                        return native_path

                root = os.path.dirname(root)

            for path in possible_paths:
                if os.path.exists(path):
                    return path
            # Nothing found: keep the old default, a `banlist.txt` next to the
            # executable, so an unrecognized layout behaves as it always did.
            default_path = os.path.join(server_dir, "banlist.txt")
            logging.debug(f"Using default banlist path: {default_path}")
            return default_path
        except Exception:
            logging.exception("Error detecting banlist path")
            return None

    def _read_lines(self):
        if not os.path.exists(self.banlist_path):
            return []

        with open(self.banlist_path, "r", encoding="utf-8") as file:
            return file.readlines()

    @staticmethod
    def _is_readable(path):
        """Return True when an existing banlist can be read."""
        try:
            with open(path, "rb"):
                return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    @staticmethod
    def _is_writable(path):
        """Return True when bans can be appended and the file replaced."""
        if os.path.exists(path):
            try:
                with open(path, "ab"):
                    pass
            except OSError:
                return False

        # Unbanning replaces the file, so the containing directory must be writable.
        directory = os.path.dirname(os.path.abspath(path))
        while directory and not os.path.isdir(directory):
            parent = os.path.dirname(directory)
            if parent == directory:
                return False
            directory = parent

        try:
            with tempfile.NamedTemporaryFile(dir=directory):
                return True
        except OSError:
            return False

    @staticmethod
    def _current_user():
        """Return the account running the monitor, or 'unknown' if unavailable."""
        try:
            return getpass.getuser()
        except Exception:
            return "unknown"

    def access_warnings(self) -> List[str]:
        """Return startup warnings for a banlist that cannot be read or written."""
        user = self._current_user()
        if not self.banlist_path:
            return [
                f"PalServer banlist not found (running as user '{user}'): banned "
                "players will not be shown and ban/unban from the dashboard will fail."
            ]

        path = self.banlist_path
        warnings = []
        if not self._is_readable(path):
            warnings.append(
                f"Cannot read the PalServer banlist at {path} (running as user "
                f"'{user}'): banned players will not be shown in the dashboard."
            )
        if not self._is_writable(path):
            warnings.append(
                f"Cannot write the PalServer banlist at {path} (running as user "
                f"'{user}'): ban and unban from the dashboard will fail. Make it "
                "writable by that account."
            )
        return warnings

    @staticmethod
    def _steam_id(line):
        line = line.strip()
        return (
            line.split(",", 1)[0].strip() if line and not line.startswith("#") else None
        )

    def get_banned_players(self) -> List[str]:
        """Read and return list of banned Steam IDs from banlist.txt.

        Returns:
            List of Steam IDs (as strings)
        """
        if not self.banlist_path:
            logging.warning("Banlist path not configured, cannot read banned players")
            return []
        with self._lock:
            try:
                banned_steam_ids = list(
                    dict.fromkeys(
                        steam_id
                        for line in self._read_lines()
                        if (steam_id := self._steam_id(line))
                    )
                )
                logging.debug(
                    f"Loaded {len(banned_steam_ids)} banned players from {self.banlist_path}"
                )
                return banned_steam_ids
            except Exception:
                logging.exception(f"Error reading banlist from {self.banlist_path}")
                return []

    def is_banned(self, steam_id: str) -> bool:
        """Check if a Steam ID is banned.

        Args:
            steam_id: Steam ID to check

        Returns:
            True if banned, False otherwise
        """
        banned_players = self.get_banned_players()
        return steam_id in banned_players

    def add_ban(self, steam_id: str) -> bool:
        """Add a Steam ID to the banlist.

        Args:
            steam_id: Steam ID to ban

        Returns:
            True if successful, False otherwise
        """
        if not self.banlist_path:
            logging.error("Banlist path not configured, cannot add ban")
            return False

        if not steam_id or any(char in steam_id for char in ",\r\n"):
            return False

        with self._lock:
            try:
                lines = self._read_lines()
                if any(self._steam_id(line) == steam_id for line in lines):
                    logging.debug(f"Steam ID {steam_id} is already banned")
                else:
                    banlist_dir = os.path.dirname(self.banlist_path)
                    if banlist_dir:
                        os.makedirs(banlist_dir, exist_ok=True)

                    with open(self.banlist_path, "a", encoding="utf-8") as f:
                        if lines and not lines[-1].endswith("\n"):
                            f.write("\n")

                        f.write(f"{steam_id}\n")
                    logging.info(f"Successfully added ban for Steam ID: {steam_id}")
            except Exception:
                logging.exception(f"Error adding ban for Steam ID {steam_id}")
                return False

        self.bus.publish(Event.BAN_ADDED, {"steam_id": steam_id})
        return True

    def remove_ban(self, steam_id: str) -> bool:
        """Remove a Steam ID from the banlist (unban).

        Args:
            steam_id: Steam ID to unban

        Returns:
            True if successful, False otherwise
        """
        if not self.banlist_path:
            logging.error("Banlist path not configured, cannot remove ban")
            return False

        if not steam_id or any(char in steam_id for char in ",\r\n"):
            return False

        temp_path = None
        with self._lock:
            try:
                lines = self._read_lines()
                remaining = [line for line in lines if self._steam_id(line) != steam_id]
                if remaining == lines:
                    logging.debug(f"Steam ID {steam_id} is not banned")
                else:
                    # Replace only after a complete write so a failed unban cannot truncate other bans.
                    with tempfile.NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        dir=os.path.dirname(os.path.abspath(self.banlist_path)),
                        delete=False,
                    ) as file:
                        temp_path = file.name
                        file.writelines(remaining)

                    os.replace(temp_path, self.banlist_path)
                    temp_path = None
                    logging.info(f"Successfully removed ban for Steam ID: {steam_id}")
            except Exception:
                logging.exception(f"Error removing ban for Steam ID {steam_id}")
                return False
            finally:
                if temp_path is not None:
                    os.unlink(temp_path)

        self.bus.publish(Event.BAN_REMOVED, {"steam_id": steam_id})
        return True
