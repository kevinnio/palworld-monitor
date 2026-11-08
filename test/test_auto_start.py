"""
Extended tests for AutoStartManager covering packet detection, socket handling,
and committed lifecycle subscriptions.
"""

import logging
import socket
import threading
from unittest.mock import MagicMock, patch

from src.auto_start import AutoStartManager
from src.events import Event, EventBus
from src.lifecycle.messages import LifecycleState
from src.settings import settings
from test.support import wait_until

FIRST_PACKET_PATTERN = b"\x09\x08\x00"


class TestAutoStartSubscriptions:
    """A committed lifecycle state starts and stops the packet listener."""

    @staticmethod
    def _stopped_controller():
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        controller.start_server.return_value = False
        return controller

    def _listening_manager(self, mock_settings):
        """An AutoStartManager whose listener is bound to an ephemeral port."""
        mock_settings.palworldServerPort = 0
        bus = EventBus()
        manager = AutoStartManager(self._stopped_controller(), bus=bus)
        manager.listen_palworld_access()
        wait_until(lambda: manager.sock is not None, message="listener never bound")
        return bus, manager

    def test_starting_lifecycle_releases_the_game_port(self, mock_settings):
        bus, manager = self._listening_manager(mock_settings)
        try:
            bus.publish(Event.LIFECYCLE_CHANGED, {"state": LifecycleState.STARTING})

            assert manager.listen_thread is None
            assert manager.sock is None
        finally:
            manager.close()

    def test_server_started_stops_the_listener(self, mock_settings):
        bus, manager = self._listening_manager(mock_settings)
        try:
            bus.publish(Event.SERVER_STARTED, {"pid": 12345, "state": "RUNNING"})

            assert manager.listen_thread is None
            assert manager.sock is None
        finally:
            manager.close()

    def test_server_stopped_rearms_the_listener(self, mock_settings):
        bus, manager = self._listening_manager(mock_settings)
        try:
            manager.stop_listen_thread()
            assert manager.listen_thread is None

            bus.publish(Event.SERVER_STOPPED, {"state": "STOPPED"})

            wait_until(
                lambda: manager.sock is not None, message="listener not re-armed"
            )
            assert manager.listen_thread is not None
        finally:
            manager.close()

    def test_close_unsubscribes_and_stops_listener(self, mock_settings):
        bus, manager = self._listening_manager(mock_settings)

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
        """A real client packet makes the listener report a connection."""
        manager = AutoStartManager(None, EventBus())
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (
            FIRST_PACKET_PATTERN,
            ("127.0.0.1", settings.palworldServerPort),
        )

        manager.sock = mock_sock
        assert manager.wait_for_player_connection() is True

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

    def test_socket_timeout_keeps_listening(self, mock_settings):
        manager = AutoStartManager(None, EventBus())
        manager.sock = MagicMock()
        manager.sock.recvfrom.side_effect = [
            socket.timeout(),
            (FIRST_PACKET_PATTERN, ("127.0.0.1", 12345)),
        ]

        assert manager.wait_for_player_connection() is True
        assert manager.sock.recvfrom.call_count == 2

    def test_idle_listener_thread_exits_on_close(self, mock_settings):
        mock_settings.palworldServerPort = 0
        manager = AutoStartManager(None, EventBus())
        assert manager.open_palworld_port_socket() is True
        assert manager.sock.gettimeout() == 0.2
        thread = threading.Thread(target=manager.wait_for_player_connection)
        manager.listen_thread = thread
        thread.start()

        try:
            manager.close()
            assert not thread.is_alive()
        finally:
            manager.close_palworld_port_socket()
            thread.join(timeout=1)

    def test_listener_start_does_not_sleep_on_the_calling_thread(self, mock_settings):
        manager = AutoStartManager(None, EventBus())

        with patch("src.auto_start.time.sleep") as sleep:
            manager.listen_palworld_access()
            manager.close()

        sleep.assert_not_called()

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
        """A running server owns the game port; the listener must not bind it."""
        controller = MagicMock()
        controller.state = LifecycleState.RUNNING
        manager = AutoStartManager(controller, EventBus())

        manager.listen_palworld_access_core()

        assert manager.sock is None
        controller.start_server.assert_not_called()

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
        """A port that cannot be bound must not start the server."""
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())

        with patch.object(manager, "open_palworld_port_socket", return_value=False):
            manager.listen_palworld_access_core()

        controller.start_server.assert_not_called()

    def test_detected_connection_starts_the_server_and_releases_the_port(
        self, mock_settings
    ):
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())

        with (
            patch.object(manager, "open_palworld_port_socket", return_value=True),
            patch.object(manager, "wait_for_player_connection", return_value=True),
        ):
            manager.listen_palworld_access_core()

        controller.start_server.assert_called_once()
        # The game port is released before the server process starts.
        assert manager.sock is None

    def test_listen_palworld_access_core_single_attempt_when_start_is_rejected(
        self, mock_settings
    ):
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        controller.start_server.return_value = False
        manager = AutoStartManager(controller, EventBus())

        with (
            patch.object(
                manager, "open_palworld_port_socket", return_value=True
            ) as open_mock,
            patch.object(manager, "close_palworld_port_socket"),
            patch.object(manager, "wait_for_player_connection", return_value=True),
        ):
            manager.listen_palworld_access_core()

        assert controller.start_server.call_count == 1
        assert open_mock.call_count == 1

    def test_server_stopped_rearms_listening(self, mock_settings):
        bus = EventBus()
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        controller.start_server.return_value = False
        manager = AutoStartManager(controller, bus)

        with (
            patch.object(
                manager, "open_palworld_port_socket", return_value=True
            ) as open_mock,
            patch.object(manager, "close_palworld_port_socket"),
            patch.object(manager, "wait_for_player_connection", return_value=True),
        ):
            manager.listen_palworld_access_core()
            assert open_mock.call_count == 1

            bus.publish(Event.SERVER_STOPPED, {})
            manager.listen_thread.join(timeout=2)

        assert open_mock.call_count == 2

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
        """Starting a new listener replaces the previous thread."""
        mock_settings.palworldServerPort = 0
        controller = MagicMock()
        controller.state = LifecycleState.STOPPED
        manager = AutoStartManager(controller, EventBus())

        try:
            manager.listen_palworld_access()
            first = manager.listen_thread
            wait_until(lambda: manager.sock is not None, message="listener never bound")

            manager.listen_palworld_access()

            assert manager.listen_thread is not first
            assert not first.is_alive()
        finally:
            manager.close()

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
