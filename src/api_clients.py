# Copyright (c) 2024 Nomomo
# Copyright (c) 2026 Kevin Perez - Modified work

import logging
import traceback

import requests
from requests.auth import HTTPBasicAuth

from src.settings import settings


def _extract_steam_id(player):
    """Extract Steam ID from a dict or raw string."""
    return player.get("steam_id") if isinstance(player, dict) else player


class RestClient:
    """REST API client for communicating with PalWorld server."""

    def __init__(self):
        self.base_url = (
            f"http://{settings.palworldServerHost}:{settings.palworldRESTPort}/v1/api"
        )
        self.headers = {"Content-Type": "application/json"}
        self.auth = HTTPBasicAuth("admin", settings.palworldServerAdminPassword)

    def _make_get_request(self, endpoint):
        try:
            url = f"{self.base_url}/{endpoint}"
            response = requests.get(
                url, headers=self.headers, auth=self.auth, timeout=10
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            logging.error(f"Error making GET request to {endpoint}: {e}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error in GET request to {endpoint}: {e}")
            logging.error(traceback.format_exc())
            return None

    def _make_post_request(self, endpoint, data=None):
        if data is None:
            data = {}
        try:
            url = f"{self.base_url}/{endpoint}"
            response = requests.post(
                url, headers=self.headers, json=data, auth=self.auth, timeout=10
            )
            if response.status_code == 200:
                return True
            logging.error(f"Failed to {endpoint} (status: {response.status_code})")
            return False
        except requests.exceptions.RequestException as e:
            logging.error(f"Error making POST request to {endpoint}: {e}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error in POST request to {endpoint}: {e}")
            logging.error(traceback.format_exc())
            return False

    def get_player_count(self):
        players = self.get_player_names()
        return len(players) if players is not None else None

    def get_player_names(self):
        try:
            players_data = self._make_get_request("players")
            if players_data is None:
                return None
            if isinstance(players_data, dict):
                players_data = players_data.get("players", [])
            if not isinstance(players_data, list):
                return None
            keys = ("name", "playerId", "userId", "level")
            return [[str(p.get(k, "Unknown")) for k in keys] for p in players_data]
        except Exception as e:
            logging.error(f"Error getting player names: {e}")
            return None

    def kick_player(self, player):
        return self._make_post_request("kick", {"userid": _extract_steam_id(player)})

    def ban_player(self, player):
        return self._make_post_request("ban", {"userid": _extract_steam_id(player)})

    def unban_player(self, player):
        return self._make_post_request("unban", {"userid": _extract_steam_id(player)})
