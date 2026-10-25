from typing import List
import os
import logging
import threading
import traceback
import tempfile
from src.settings import settings
from src.events import bus, Event


class BanlistManager:
    """Manages the Palworld server banlist.txt file."""

    def __init__(self, banlist_path=None):
        """Initialize the banlist manager.

        Args:
            banlist_path: Optional path to banlist.txt. If None, will try to auto-detect.
        """
        self.banlist_path = banlist_path or self._detect_banlist_path()
        self._lock = threading.RLock()

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
            possible_paths = [
                os.path.join(server_dir, "banlist.txt"),
                os.path.join(server_dir, "Pal", "Saved", "SaveGames", "banlist.txt"),
                os.path.join(
                    server_dir, "Pal", "Saved", "SaveGames", "0", "banlist.txt"
                ),
                os.path.join(server_dir, "Pal", "Saved", "banlist.txt"),
            ]
            root = server_dir
            for _ in range(4):
                if os.path.isdir(os.path.join(root, "Pal", "Binaries")):
                    native_path = os.path.join(
                        root, "Pal", "Saved", "SaveGames", "banlist.txt"
                    )
                    return native_path

                root = os.path.dirname(root)

            for path in possible_paths:
                if os.path.exists(path):
                    return path
            default_path = possible_paths[0]
            logging.debug(f"Using default banlist path: {default_path}")
            return default_path
        except Exception as e:
            logging.error(f"Error detecting banlist path: {e}")
            logging.error(traceback.format_exc())
            return None

    def _read_lines(self):
        if not os.path.exists(self.banlist_path):
            return []

        with open(self.banlist_path, "r", encoding="utf-8") as file:
            return file.readlines()

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
            except Exception as e:
                logging.error(f"Error reading banlist from {self.banlist_path}: {e}")
                logging.error(traceback.format_exc())
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
            except Exception as e:
                logging.error(f"Error adding ban for Steam ID {steam_id}: {e}")
                logging.error(traceback.format_exc())
                return False

        bus.publish(Event.BAN_ADDED, {"steam_id": steam_id})
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
            except Exception as e:
                logging.error(f"Error removing ban for Steam ID {steam_id}: {e}")
                logging.error(traceback.format_exc())
                return False
            finally:
                if temp_path is not None:
                    os.unlink(temp_path)

        bus.publish(Event.BAN_REMOVED, {"steam_id": steam_id})
        return True
