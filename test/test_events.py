"""
Tests for the EventBus and Event constants.
"""

from src.events import EventBus, Event


class TestEventBus:
    """Test suite for EventBus."""

    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe("TEST_EVENT", lambda data: received.append(data))
        bus.publish("TEST_EVENT", {"key": "value"})
        assert received == [{"key": "value"}]

    def test_multiple_subscribers(self):
        bus = EventBus()
        results = []
        bus.subscribe("MULTI_EVENT", lambda data: results.append(("a", data)))
        bus.subscribe("MULTI_EVENT", lambda data: results.append(("b", data)))
        bus.publish("MULTI_EVENT", {"data": 1})
        assert len(results) == 2
        assert ("a", {"data": 1}) in results
        assert ("b", {"data": 1}) in results

    def test_no_subscribers(self):
        bus = EventBus()
        # Should not raise any exception
        bus.publish("NONEXISTENT_EVENT", {})

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        received = []

        def callback(data):
            received.append(data)

        bus.subscribe("REMOVE_TEST", callback)
        bus.publish("REMOVE_TEST", {"step": 1})
        assert received == [{"step": 1}]

        bus.unsubscribe("REMOVE_TEST", callback)
        bus.publish("REMOVE_TEST", {"step": 2})
        assert received == [{"step": 1}]
        assert bus.subscriber_count("REMOVE_TEST") == 0

    def test_unsubscribe_unknown_callback_is_a_noop(self):
        bus = EventBus()
        bus.unsubscribe("UNKNOWN", lambda data: None)
        assert bus.subscriber_count() == 0

    def test_subscriber_count(self):
        bus = EventBus()
        bus.subscribe("A", lambda data: None)
        bus.subscribe("A", lambda data: None)
        bus.subscribe("B", lambda data: None)
        assert bus.subscriber_count("A") == 2
        assert bus.subscriber_count() == 3

    def test_subscriber_error_does_not_break_others(self):
        bus = EventBus()
        results = []
        bus.subscribe(
            "ERROR_TEST", lambda data: (_ for _ in ()).throw(ValueError("boom"))
        )
        bus.subscribe("ERROR_TEST", lambda data: results.append(data))
        bus.publish("ERROR_TEST", {"data": 1})
        assert results == [{"data": 1}]

    def test_event_constants(self):
        assert Event.SERVER_STARTED == "SERVER_STARTED"
        assert Event.SERVER_STOPPED == "SERVER_STOPPED"
        assert Event.SERVER_STATUS == "SERVER_STATUS"
        assert Event.LIFECYCLE_CHANGED == "LIFECYCLE_CHANGED"
        assert Event.PLAYER_JOINED == "PLAYER_JOINED"
        assert Event.PLAYER_LEFT == "PLAYER_LEFT"
        assert Event.BAN_ADDED == "BAN_ADDED"
        assert Event.BAN_REMOVED == "BAN_REMOVED"


class TestEventSubscriptionPatterns:
    """Test common subscription patterns used across the codebase."""

    def test_server_started_handler_pattern(self):
        bus = EventBus()
        call_count = [0]

        def on_server_started(data):
            call_count[0] += 1
            assert data["state"] == "RUNNING"

        bus.subscribe(Event.SERVER_STARTED, on_server_started)
        bus.publish(Event.SERVER_STARTED, {"pid": 12345, "state": "RUNNING"})
        assert call_count[0] == 1

    def test_committed_status_pattern(self):
        bus = EventBus()
        received_data = []

        def on_status(data):
            received_data.append(data)

        bus.subscribe(Event.SERVER_STATUS, on_status)
        bus.publish(
            Event.SERVER_STATUS,
            {
                "state": "RUNNING",
                "playerData": "ok",
                "playerCount": 0,
                "banned_players": [],
            },
        )
        assert len(received_data) == 1
        assert received_data[0]["playerData"] == "ok"

    def test_player_join_event_pattern(self):
        bus = EventBus()
        joined_ids = []

        def on_player_joined(data):
            joined_ids.append(data["steam_id"])

        bus.subscribe(Event.PLAYER_JOINED, on_player_joined)
        bus.publish(Event.PLAYER_JOINED, {"steam_id": "12345", "name": "Test"})
        assert joined_ids == ["12345"]

    def test_ban_events_pattern(self):
        bus = EventBus()
        banned = []
        unbanned = []

        bus.subscribe(Event.BAN_ADDED, lambda data: banned.append(data["steam_id"]))
        bus.subscribe(Event.BAN_REMOVED, lambda data: unbanned.append(data["steam_id"]))

        bus.publish(Event.BAN_ADDED, {"steam_id": "99999"})
        bus.publish(Event.BAN_REMOVED, {"steam_id": "99999"})

        assert banned == ["99999"]
        assert unbanned == ["99999"]
