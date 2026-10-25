"""Dummy PalServer — a minimal REST API mimicking the real PalServer API.

Serves fake player data on port 8212 and supports kick / ban / unban
actions. Used by e2e smoke tests so they don't need a real server.
"""

import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from base64 import b64decode

HOST = "127.0.0.1"
PORT = 8212
USERNAME = "admin"
PASSWORD = "palworld123"

_player_count = int(os.environ.get("DUMMY_PLAYER_COUNT", "3"))

_PLAYERS_TEMPLATE = [
    {"name": "TestPlayer1", "playerId": "steam_001", "userId": "steam_001", "level": 15},
    {"name": "TestPlayer2", "playerId": "steam_002", "userId": "steam_002", "level": 42},
    {"name": "TestPlayer3", "playerId": "steam_003", "userId": "steam_003", "level": 7},
]
_players = list(_PLAYERS_TEMPLATE[:_player_count])
_lock = threading.Lock()


def _check_auth(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        creds = b64decode(auth[6:]).decode()
        user, pwd = creds.split(":", 1)
        return user == USERNAME and pwd == PASSWORD
    except Exception:
        return False


def _find_player(userid):
    with _lock:
        for p in _players:
            if p["playerId"] == userid:
                return p
    return None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silent

    def _respond(self, code, body=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body is not None:
            self.wfile.write(json.dumps(body).encode())

    def _require_auth(self):
        if not _check_auth(self.headers):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Palworld"')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self._require_auth():
            return
        if self.path == "/v1/api/players":
            with _lock:
                self._respond(200, {"players": list(_players)})
        elif self.path == "/v1/api/info":
            self._respond(200, {
                "version": "0.0.0",
                "servername": "PalServer-Dummy",
                "description": "",
                "worldguid": "00000000-0000-0000-0000-000000000000",
            })
        elif self.path == "/v1/api/metrics":
            self._respond(200, {
                "serverfps": 60,
                "currentplayernum": len(_players),
                "serverframetime": 16.6,
                "maxplayernum": 32,
                "uptime": 12345,
                "basecampnum": 0,
                "days": 42,
            })
        elif self.path == "/v1/api/settings":
            self._respond(200, {
                "description": "Dummy server for e2e smoke tests",
                "difficulty": "None",
                "servername": "PalServer-Dummy",
                "serverpassword": "",
                "version": "0.0.0",
            })
        else:
            self._respond(404, {})

    def do_POST(self):
        if not self._require_auth():
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}

        userid = body.get("userid", "")
        endpoint = self.path[len("/v1/api/"):] if self.path.startswith("/v1/api/") else ""

        if endpoint == "kick":
            p = _find_player(userid)
            if p:
                with _lock:
                    _players.remove(p)
            self._respond(200)
        elif endpoint == "ban":
            p = _find_player(userid)
            if p:
                with _lock:
                    _players.remove(p)
            self._respond(200)
        elif endpoint == "unban":
            self._respond(200)
        elif endpoint == "announce":
            self._respond(200, {})
        else:
            self._respond(404, {})


def main():
    server = HTTPServer((HOST, PORT), _Handler)
    # Run in the main thread so the process stays alive.
    server.serve_forever()


if __name__ == "__main__":
    main()

