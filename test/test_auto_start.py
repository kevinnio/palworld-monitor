"""
Extended tests for AutoStartManager covering packet detection, socket handling,
and committed lifecycle subscriptions.
"""

import logging
import types
from unittest.mock import MagicMock, patch

from src.auto_start import AutoStartManager
from src.events import Event, EventBus
from src.palworld_control import LifecycleState
from src.settings import settings

FIRST_PACKET_PATTERN = b"\x09\x08\x00"


class RecordingBus(EventBus):
    def __init__(self):
        super().__init__()
        self.subscriptions = []

    def subscribe(self, event_type, callback):
        self.subscriptions.append((event_type, callback))
        super().subscribe(event_type, callback)


class TestAutoStartSubscriptions:
    """Test suite for committed lifecycle subscriptions."""

    def test_init_subscribes_to_server_events(self):
        bus = RecordingBus()
        manager = AutoStartManager(None, bus=bus)

        assert (Event.SERVER_STARTED, manager.stop_listen_thread) in bus.subscriptions
        assert (
            Event.SERVER_STOPPED,
            manager.listen_palworld_access,
        ) in bus.subscriptions
        assert (Event.LIFECYCLE_CHANGED, manager._on_lifecycle_changed) in (
            bus.subscriptions
        )

    def test_starting_lifecycle_stops_the_listener_before_launch(self):
        bus = EventBus()
        manager = AutoStartManager(None, bus=bus)

        with patch.object(manager, "stop_listen_thread") as stop:
            bus.publish(Event.LIFECYCLE_CHANGED, {"state": "STARTING"})

        stop.assert_called_once()

    def test_server_started_handler_is_stop_listen_thread(self):
        bus = RecordingBus()
        manager = AutoStartManager(None, bus=bus)

        handler = dict(bus.subscriptions)[Event.SERVER_STARTED]
        assert isinstance(handler, types.MethodType)
        assert handler.__self__ is manager
        assert handler.__func__.__name__ == "stop_listen_thread"

    def test_server_stopped_handler_is_listen_palworld_access(self):
        bus = RecordingBus()
        manager = AutoStartManager(None, bus=bus)

        handler = dict(bus.subscriptions)[Event.SERVER_STOPPED]
        assert isinstance(handler, types.MethodType)
        assert handler.__self__ is manager
        assert handler.__func__.__name__ == "listen_palworld_access"

    def test_close_unsubscribes_and_stops_listener(self):
        bus = RecordingBus()
        manager = AutoStartManager(None, bus=bus)

        manager.close()

        assert bus.subscriber_count() == 0
        assert manager.listen_thread is None


class TestAutoStartPacketDetection:
    """Test suite for _is_player_connection_packet."""

    def test_valid_player_packet(self, mock_settings):
        """Test detection of a valid player connection packet."""
        manager = AutoStartManager(None, EventBus())
        # The first packet from Palworld clients starts with \x09\x08\x00
        assert manager._is_player_connection_packet(b"\x09\x08\x00") is True

    def test_invalid_packet(self, mock_settings):
        """Test rejection of non-player packets."""
        manager = AutoStartManager(None, EventBus())
        assert manager._is_player_connection_packet(b"\x00\x00\x00") is False

    def test_empty_data(self, mock_settings):
        """Test handling of empty data."""
        manager = AutoStartManager(None, EventBus())
        assert manager._is_player_connection_packet(b"") is False

    def test_partial_match(self, mock_settings):
        """Test that partial matches to the pattern are rejected."""
        manager = AutoStartManager(None, EventBus())
        assert manager._is_player_connection_packet(b"\x09\x00\x00") is False

    def test_longer_valid_packet(self, mock_settings):
        """Test detection of a longer valid packet (with payload after header)."""
        manager = AutoStartManager(None, EventBus())
        assert (
            manager._is_player_connection_packet(b"\x09\x08\x00" + b"\x01" * 50) is True
        )

    def test_random_garbage_rejected(self, mock_settings):
        """Test that random garbage data is rejected."""
        import os

        manager = AutoStartManager(None, EventBus())
        assert manager._is_player_connection_packet(os.urandom(100)) is False


class TestAutoStartSocketHandling:
    """Test suite for socket-related methods."""

    def test_close_palworld_port_socket_no_sock(self, mock_settings):
        """Test closing when no socket exists."""
        manager = AutoStartManager(None, EventBus())
        result = manager.close_palworld_port_socket()
        assert result is True  # No-op should succeed

    def test_wait_for_player_connection_with_packet(self, mock_settings):
        """Test wait_for_player_connection with a valid packet."""
        manager = AutoStartManager(None, EventBus())
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (
            FIRST_PACKET_PATTERN,
            ("127.0.0.1", settings.palworldServerPort),
        )

        with patch.object(manager, "_is_player_connection_packet", return_value=True):
            manager.sock = mock_sock
            result = manager.wait_for_player_connection()
            assert result is True

    def test_wait_for_player_connection_socket_none(self, mock_settings):
        """Test wait_for_player_connection when socket is None."""
        manager = AutoStartManager(None, EventBus())
        manager.sock = None
        result = manager.wait_for_player_connection()
        assert result is False

    def test_wait_for_player_connection_aborting(self, mock_settings):
        """Test wait_for_player_connection when aborting."""
        manager = AutoStartManager(None, EventBus())
        manager.is_aborting = True
        result = manager.wait_for_player_connection()
        assert result is False

    def test_closed_socket_is_not_an_error(self, mock_settings, caplog):
        """A listener socket closed by stop_listen_thread (server started
        manually) must not log an error with traceback."""
        manager = AutoStartManager(None, bus=EventBus())

        def recvfrom_closing(_size):
            manager.is_aborting = True  # what close_palworld_port_socket does
            raise OSError(10038, "not a socket")

        manager.sock = MagicMock()
        manager.sock.recvfrom.side_effect = recvfrom_closing

        with caplog.at_level(logging.DEBUG):
            result = manager.wait_for_player_connection()

        assert result is False
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_listen_palworld_access_core_server_running(self, mock_settings):
        """Test listen_palworld_access_core when the server is already running."""
        controller = MagicMock()
        controller.state = LifecycleState.RUNNING
        manager = AutoStartManager(controller, EventBus())
        result = manager.listen_palworld_access_core()
        assert result is None  # Returns None (no explicit return)

    def test_listen_palworld_access_core_starting_is_not_listened_too(
        self, mock_settings
    ):
        controller = MagicMock()
        controller.state = LifecycleState.STARTING
        manager = AutoStartManager(controller, EventBus())

        with patch.object(manager, "open_palworld_port_socket") as mock_open:
            manager.listen_palworld_access_core()

        mock_open.assert_not_called()

    def test_listen_palworld_access_starts_thread(self, mock_settings):
        """Test that listen_palworld_access starts a new thread."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())
        manager.listen_palworld_access()
        assert manager.listen_thread is not None
        assert manager.listen_thread.is_alive()
        manager.stop_listen_thread()

    def test_stop_listen_thread(self, mock_settings):
        """Test stopping the listen thread."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())
        manager.listen_palworld_access()
        manager.stop_listen_thread()
        assert manager.listen_thread is None

    def test_listen_palworld_access_core_socket_failure(self, mock_settings):
        """Test listen_palworld_access_core when socket opening fails."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())

        with patch.object(manager, "open_palworld_port_socket", return_value=False):
            result = manager.listen_palworld_access_core()
            assert result is None  # Returns None on failure

    def test_listen_palworld_access_core_start_failure(self, mock_settings):
        """Test listen_palworld_access_core when server start fails."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())

        with patch.object(manager, "wait_for_player_connection", return_value=True):
            with patch("time.sleep", return_value=None):
                manager.listen_palworld_access_core()
                assert controller.start_server.called

    def test_listen_palworld_access_core_rebinds_when_start_is_rejected(
        self, mock_settings
    ):
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        controller.start_server.return_value = False
        manager = AutoStartManager(controller, EventBus())

        with (
            patch.object(manager, "open_palworld_port_socket", return_value=True) as open_mock,
            patch.object(manager, "close_palworld_port_socket"),
            patch.object(manager, "wait_for_player_connection", return_value=True),
        ):
            manager.listen_palworld_access_core()

        assert controller.start_server.call_count == 3
        assert open_mock.call_count == 3

    def test_listen_palworld_access_core_success(self, mock_settings):
        """Test listen_palworld_access_core successful flow."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        controller.start_server.side_effect = lambda: setattr(
            controller, "state", LifecycleState.RUNNING
        )
        manager = AutoStartManager(controller, EventBus())

        with patch.object(manager, "wait_for_player_connection", return_value=True):
            with patch("time.sleep", return_value=None):
                manager.listen_palworld_access_core()
                assert controller.start_server.called


class TestAutoStartAbortFlow:
    """Test suite for abort and cleanup flows."""

    def test_is_aborting_flag_set_on_close(self, mock_settings):
        """Test that close_palworld_port_socket sets is_aborting flag."""
        manager = AutoStartManager(None, EventBus())
        manager.close_palworld_port_socket()
        assert manager.is_aborting is True

    def test_listen_palworld_access_stops_existing_thread(self, mock_settings):
        """Test that listen_palworld_access stops any existing thread first."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())

        manager.listen_palworld_access()
        assert manager.listen_thread is not None

        manager.listen_palworld_access()
        assert manager.listen_thread is not None

        manager.stop_listen_thread()

    def test_wait_for_player_connection_returns_false_on_aborted(self, mock_settings):
        """Test that wait_for_player_connection returns False when aborted."""
        manager = AutoStartManager(None, EventBus())
        manager.is_aborting = True
        result = manager.wait_for_player_connection()
        assert result is False

    def test_listen_palworld_access_resets_stale_is_aborting_flag(self, mock_settings):
        """Regression test: a cycle that ended by successfully starting the
        server leaves is_aborting True; the next cycle must reset it or it
        would return immediately without ever binding the socket."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())
        manager.is_aborting = True  # leftover from a prior successful cycle

        with patch.object(
            manager, "open_palworld_port_socket", return_value=False
        ) as mock_open:
            manager.listen_palworld_access()
            assert manager.is_aborting is False
            manager.listen_thread.join(timeout=2)

        assert mock_open.called
