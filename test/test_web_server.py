"""Tests for the committed-snapshot web layer."""

import json
import queue
from unittest.mock import MagicMock

from src.events import Event, EventBus
from src.web_server import WebServer


def make_snapshot(**overrides):
    snapshot = {
        "revision": 0,
        "state": "STOPPED",
        "playerData": "unknown",
        "playerCount": 0,
        "players": [],
        "banned_players": [],
    }
    snapshot.update(overrides)
    return snapshot


def make_server(mock_settings, snapshot=None):
    bus = EventBus()
    controller = MagicMock()
    controller.get_snapshot.return_value = snapshot or make_snapshot()
    server = WebServer(controller, bus=bus)
    server.app.config["WTF_CSRF_ENABLED"] = False
    return server, controller, bus


class TestWebServerCommittedSnapshots:
    def test_initial_cache_is_one_controller_snapshot(self, mock_settings):
        snapshot = make_snapshot(
            state="RUNNING",
            playerData="ok",
            playerCount=1,
            players=[
                {
                    "name": "P1",
                    "steam_id": "111",
                    "level": "10",
                    "currently_online": True,
                }
            ],
            banned_players=["999"],
        )
        server, controller, _ = make_server(mock_settings, snapshot)

        assert controller.get_snapshot.call_count == 1
        assert server.snapshot == snapshot

    def test_committed_event_refreshes_and_broadcasts_the_same_snapshot(
        self, mock_settings
    ):
        server, controller, bus = make_server(mock_settings)
        sse_queue = queue.Queue()
        server._sse_clients.append(sse_queue)

        updated = make_snapshot(
            revision=1,
            state="RUNNING",
            playerData="ok",
            playerCount=2,
            players=[
                {
                    "name": "P1",
                    "steam_id": "111",
                    "level": "10",
                    "currently_online": True,
                },
                {
                    "name": "P2",
                    "steam_id": "222",
                    "level": "20",
                    "currently_online": True,
                },
            ],
        )
        controller.get_snapshot.return_value = updated
        controller.get_snapshot.reset_mock()

        bus.publish(Event.SERVER_STATUS, {"state": "RUNNING", "generation": 3})

        assert controller.get_snapshot.call_count == 1
        assert server.snapshot == updated
        payload = json.loads(sse_queue.get_nowait().removeprefix("data: ").strip())
        assert payload["data"] == updated
        assert payload["data"]["state"] == "RUNNING"
        assert payload["data"]["playerData"] == "ok"
        assert payload["autoStopDelay"] == round(mock_settings.autoStopDelay)

    def test_json_state_reads_exactly_one_snapshot(self, mock_settings):
        snapshot = make_snapshot(
            revision=2,
            state="STOPPING",
            playerData="unavailable",
            playerCount=1,
            players=[
                {
                    "name": "P1",
                    "steam_id": "111",
                    "level": "10",
                    "currently_online": True,
                }
            ],
            banned_players=["999"],
        )
        server, controller, _ = make_server(mock_settings, snapshot)
        controller.get_snapshot.reset_mock()

        with server.app.test_request_context():
            response = server._json_state()

        body = json.loads(response.data)
        assert controller.get_snapshot.call_count == 1
        assert body["data"] == snapshot
        assert body["data"]["state"] == "STOPPING"
        assert body["data"]["playerData"] == "unavailable"
        assert body["data"]["players"] == snapshot["players"]
        assert body["data"]["banned_players"] == ["999"]

    def test_ban_event_refreshes_snapshot(self, mock_settings):
        server, controller, bus = make_server(mock_settings)
        controller.get_snapshot.return_value = make_snapshot(banned_players=["999"])

        bus.publish(Event.BAN_ADDED, {"steam_id": "999"})

        assert server.snapshot["banned_players"] == ["999"]

    def test_lifecycle_event_refreshes_snapshot(self, mock_settings):
        server, controller, bus = make_server(mock_settings)
        controller.get_snapshot.return_value = make_snapshot(state="STARTING")

        bus.publish(Event.LIFECYCLE_CHANGED, {"state": "STARTING", "generation": 2})

        assert server.snapshot["state"] == "STARTING"

    def test_close_unsubscribes_idempotently(self, mock_settings):
        server, _, bus = make_server(mock_settings)
        baseline = bus.subscriber_count()
        assert baseline == 6

        server.close()
        assert bus.subscriber_count() == 0

        server.close()
        assert bus.subscriber_count() == 0

    def test_stream_initial_payload_is_the_committed_snapshot(self, mock_settings):
        snapshot = make_snapshot(
            revision=3,
            state="RUNNING",
            playerData="unavailable",
            playerCount=1,
            players=[
                {
                    "name": "P1",
                    "steam_id": "111",
                    "level": "10",
                    "currently_online": True,
                }
            ],
            banned_players=["999"],
        )
        server, controller, _ = make_server(mock_settings, snapshot)
        controller.get_snapshot.reset_mock()

        with server.app.test_request_context():
            response = server._handle_stream()
            first = next(response.response)

        payload = json.loads(first.removeprefix("data: ").strip())
        assert controller.get_snapshot.call_count == 1
        assert payload["data"] == snapshot
        assert payload["data"]["state"] == "RUNNING"
        assert payload["data"]["playerData"] == "unavailable"
        response.response.close()

    def test_stale_revision_is_not_broadcast(self, mock_settings):
        current = make_snapshot(revision=3, state="RUNNING")
        server, controller, _ = make_server(mock_settings, current)
        sse_queue = queue.Queue()
        server._sse_clients.append(sse_queue)

        controller.get_snapshot.return_value = make_snapshot(
            revision=2, state="STOPPED"
        )
        server._on_committed({})

        assert sse_queue.empty()
        assert server.snapshot["revision"] == 3
        assert server.snapshot["state"] == "RUNNING"

    def test_equal_revision_ban_refresh_is_applied(self, mock_settings):
        server, controller, bus = make_server(
            mock_settings, make_snapshot(revision=3)
        )
        sse_queue = queue.Queue()
        server._sse_clients.append(sse_queue)
        controller.get_snapshot.return_value = make_snapshot(
            revision=3, banned_players=["999"]
        )

        bus.publish(Event.BAN_ADDED, {"steam_id": "999"})

        assert server.snapshot["banned_players"] == ["999"]
        payload = json.loads(sse_queue.get_nowait().removeprefix("data: ").strip())
        assert payload["data"]["banned_players"] == ["999"]

    def test_initial_stream_snapshot_precedes_queued_updates(self, mock_settings):
        server, controller, bus = make_server(
            mock_settings, make_snapshot(revision=1, state="STOPPED")
        )
        response = server._handle_stream()

        # An event at the current revision queues an update.
        bus.publish(Event.BAN_ADDED, {"steam_id": "111"})
        # A newer committed snapshot queues a later update.
        controller.get_snapshot.return_value = make_snapshot(
            revision=2, state="RUNNING"
        )
        bus.publish(Event.SERVER_STARTED, {"pid": 42})

        revisions = []
        for _ in range(3):
            payload = json.loads(
                next(response.response).removeprefix("data: ").strip()
            )
            revisions.append(payload["data"]["revision"])
        response.response.close()

        assert revisions == [1, 1, 2]

    def test_get_banned_uses_committed_snapshot(self, mock_settings):
        snapshot = make_snapshot(banned_players=["111", "222"])
        server, controller, _ = make_server(mock_settings, snapshot)

        with server.app.test_request_context():
            response = server._handle_get_banned()

        body = json.loads(response.data)
        assert body == {"success": True, "banned_players": ["111", "222"]}
