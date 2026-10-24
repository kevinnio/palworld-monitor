# Copyright (c) 2024 Nomomo
# Copyright (c) 2026 Kevin Perez - Modified work
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

"""Auto-start manager: listens for Palworld client packets and launches the server."""

import socket
from src.events import EventBus, Event
from src.settings import settings
from src.palworld_control import LifecycleState, PalWorldController
import logging
import threading
import traceback
from typing import Optional
import time


class AutoStartManager:
    def __init__(
        self,
        palworld_controller: Optional[PalWorldController],
        bus: EventBus,
    ):
        self.controller: Optional[PalWorldController] = palworld_controller
        self.bus = bus
        self.sock = None
        self.is_aborting = False
        self.listen_thread = None
        self._lock = threading.Lock()
        self._subscribed = False
        self._subscribe()

    def _subscribe(self):
        if self._subscribed:
            return
        self.bus.subscribe(Event.SERVER_STARTED, self.stop_listen_thread)
        self.bus.subscribe(Event.SERVER_STOPPED, self.listen_palworld_access)
        self.bus.subscribe(Event.LIFECYCLE_CHANGED, self._on_lifecycle_changed)
        self._subscribed = True

    def _on_lifecycle_changed(self, data):
        if data.get("state") == LifecycleState.STARTING:
            # Release the game port before the server process starts.
            self.stop_listen_thread()

    def close(self):
        """Stop listening and unsubscribe from committed lifecycle events."""
        self.stop_listen_thread()
        if self._subscribed:
            self.bus.unsubscribe(Event.SERVER_STARTED, self.stop_listen_thread)
            self.bus.unsubscribe(Event.SERVER_STOPPED, self.listen_palworld_access)
            self.bus.unsubscribe(Event.LIFECYCLE_CHANGED, self._on_lifecycle_changed)
            self._subscribed = False

    def is_port_available(self, port):
        """Check if PalWorld server port is available."""
        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
            test_socket.bind((settings.palworldServerHost, port))
            test_socket.close()
            return True
        except OSError:
            return False

    def open_palworld_port_socket(self):
        """Open socket before listen. Thread-safe."""
        palworld_server_port = settings.palworldServerPort
        max_retries = 5
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                new_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                new_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    new_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except (OSError, AttributeError):
                    pass
                new_sock.bind(("0.0.0.0", palworld_server_port))

                with self._lock:
                    if self.is_aborting:
                        new_sock.close()
                        return False
                    self.sock = new_sock
                    self.is_aborting = False
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    logging.error(
                        f"Palworld port {palworld_server_port} is still in use. Cannot bind to port: {e}"
                    )
                    time.sleep(retry_delay)
                    continue
                else:
                    logging.error(f"Error opening PalWorld port socket: {e}")
                    logging.error(traceback.format_exc())
                    return False
        return False

    def close_palworld_port_socket(self):
        """Close socket. Thread-safe."""
        with self._lock:
            self.is_aborting = True
            sock = self.sock
            self.sock = None

        if sock is None:
            return True
        logging.debug("No longer listening on PalWorld Server port")
        try:
            sock.close()
        except Exception as e:
            logging.error(f"Error closing PalWorld port socket: {e}")
            logging.error(traceback.format_exc())
            return False
        return True

    def wait_for_port_available(self, port, timeout=30):
        """Wait up to `timeout` seconds for the port to become available. Returns True if available, False if timeout."""
        start_time = time.time()
        logging.info(f"Waiting for Palworld port {port} to become available...")
        while not self.is_port_available(port):
            if time.time() - start_time > timeout:
                logging.error(
                    f"Palworld port {port} is still in use after waiting {timeout} seconds. Aborting listen."
                )
                return False
            time.sleep(1)
        return True

    def wait_for_player_connection(self):
        """Wait for a player connection packet. Returns True if detected, False otherwise."""
        while not self.is_aborting:
            with self._lock:
                sock = self.sock
            if sock is None:
                return False
            try:
                data, _addr = sock.recvfrom(1024)
                if self._is_player_connection_packet(data):
                    logging.info("A player is attempting to connect. Starting Palworld Server...")
                    return True
            except OSError as e:
                # Closing the socket from another thread (server started
                # manually) is the normal way this listener shuts down.
                if self.is_aborting:
                    logging.debug(f"Auto-start listener closed: {e}")
                    return False
                logging.error(f"Error in wait_for_player_connection: {e}")
                logging.error(traceback.format_exc())
                return False
            except Exception as e:
                logging.error(f"Error in wait_for_player_connection: {e}")
                logging.error(traceback.format_exc())
                return False
        return False

    def _is_player_connection_packet(self, data):
        """Check if the received data is a player connection packet."""
        return data.startswith(b"\x09\x08\x00")

    def listen_palworld_access_core(self):
        """Listen from PalWorld server port."""
        if self.controller is None or self.controller.state != LifecycleState.STOPPED:
            return

        for _attempt in range(3):
            if self.is_aborting:
                return

            if not self.open_palworld_port_socket():
                return

            if not self.wait_for_player_connection():
                self.close_palworld_port_socket()
                return

            # The server is about to start; release the port and let the
            # supervisor's committed state re-arm listening if the launch fails.
            self.close_palworld_port_socket()
            if self.controller.start_server():
                return

    def listen_palworld_access(self, data=None):
        """Start listening for PalWorld access."""
        # Stop any existing listen thread
        self.stop_listen_thread()

        # Add a small delay to allow the port to be released
        time.sleep(1)

        # `is_aborting` is only ever cleared inside open_palworld_port_socket()'s
        # success path. If the *previous* cycle ended by successfully starting
        # the server (the common case), that path never re-ran, so the flag is
        # still True from the close_palworld_port_socket() call that preceded
        # it -- which would make every future listen_palworld_access_core()
        # call return immediately on its very first `if self.is_aborting`
        # check, without ever binding the socket or logging anything. Reset it
        # here so each new cycle starts clean.
        with self._lock:
            self.is_aborting = False

        # Start new listen thread
        self.listen_thread = threading.Thread(target=self.listen_palworld_access_core)
        self.listen_thread.daemon = (
            True  # Make thread daemon so it exits when main process exits
        )
        self.listen_thread.start()

    def stop_listen_thread(self, data=None):
        """Stop the listen thread if it's running."""
        if self.listen_thread and self.listen_thread.is_alive():
            logging.debug("Stopping auto-start listen thread...")
            self.is_aborting = True
            self.close_palworld_port_socket()

            # Don't join if we're in the same thread to avoid deadlock
            if threading.current_thread() != self.listen_thread:
                self.listen_thread.join(timeout=5)
            else:
                logging.debug("Skipping thread join to avoid deadlock")

            self.listen_thread = None
            logging.debug("Auto-start listen thread stopped.")
