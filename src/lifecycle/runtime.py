"""Lifecycle runtime: queues, workers, command waiting and thread lifecycle.

Workers perform blocking I/O and submit messages. Only the single lifecycle
thread calls the supervisor; this module never mutates lifecycle state itself.
"""

import logging
import queue
import threading

from src.lifecycle.messages import (
    LaunchRequest,
    LaunchResult,
    PollRequest,
    ProcessObservation,
    RosterSample,
    StopRequest,
    StopResult,
)
from src.lifecycle.supervisor import Supervisor
from src.settings import settings

_SHUTDOWN = object()
_WORKER_JOIN_TIMEOUT = 5


class LifecycleRuntime:
    def __init__(self, supervisor: Supervisor, client, process_manager):
        self.supervisor = supervisor
        self.client = client
        self.process_manager = process_manager

        self._observations: queue.Queue = queue.Queue()
        self._poll_requests: queue.Queue = queue.Queue()
        self._process_requests: queue.Queue = queue.Queue()

        self._closing = threading.Event()
        self._started = False
        self._start_lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def start(self):
        """Start the lifecycle thread and workers. Idempotent."""
        with self._start_lock:
            if self._started:
                return

            if any(thread.is_alive() for thread in self._threads.values()):
                raise RuntimeError("Lifecycle workers are still shutting down")

            # No workers remain. Preserve pending work, not shutdown markers.
            for requests in (
                self._observations,
                self._poll_requests,
                self._process_requests,
            ):
                for _ in range(requests.qsize()):
                    request = requests.get_nowait()
                    if request is not _SHUTDOWN:
                        requests.put(request)

            self._started = True
            self._closing.clear()
            self._threads = {
                "palworld-lifecycle": threading.Thread(
                    target=self._supervisor_loop,
                    name="palworld-lifecycle",
                    daemon=True,
                ),
                "palworld-detection": threading.Thread(
                    target=self._detection_worker,
                    name="palworld-detection",
                    daemon=True,
                ),
                "palworld-poll": threading.Thread(
                    target=self._poll_worker, name="palworld-poll", daemon=True
                ),
                "palworld-process": threading.Thread(
                    target=self._process_worker, name="palworld-process", daemon=True
                ),
            }
            for thread in self._threads.values():
                thread.start()

    def close(self):
        """Stop the workers and release process resources. Idempotent."""
        with self._start_lock:
            if not self._started:
                return
            self._started = False
            self._closing.set()
            self._observations.put(_SHUTDOWN)
            for requests in (self._poll_requests, self._process_requests):
                requests.put(_SHUTDOWN)
            threads = list(self._threads.values())

        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(timeout=_WORKER_JOIN_TIMEOUT)
            if thread.is_alive():
                logging.warning(f"Thread {thread.name} did not stop in time")

        close_process_manager = getattr(self.process_manager, "close", None)
        if close_process_manager is not None:
            close_process_manager()

    def submit_command(self, command) -> bool:
        if not self._started or self._closing.is_set():
            logging.warning("Controller is not running; command ignored")
            return False
        self._observations.put(command)
        if not command.done.wait(timeout=5):
            logging.error("Lifecycle command timed out")
            return False
        return command.result

    # ------------------------------------------------------------------
    # Lifecycle thread
    # ------------------------------------------------------------------

    def _supervisor_loop(self):
        while not self._closing.is_set():
            timeout = self.supervisor.next_timeout()
            try:
                item = self._observations.get(timeout=timeout)
            except queue.Empty:
                try:
                    requests = self.supervisor.on_tick()
                except Exception:
                    logging.exception("Lifecycle supervisor tick failed")
                    continue
            else:
                if item is _SHUTDOWN:
                    break
                try:
                    requests = self.supervisor.handle(item)
                except Exception:
                    logging.exception(
                        "Lifecycle supervisor failed to handle an observation"
                    )
                    continue
            for request in requests:
                self._dispatch(request)

    def _dispatch(self, request):
        if isinstance(request, PollRequest):
            self._poll_requests.put(request)
        elif isinstance(request, (LaunchRequest, StopRequest)):
            self._process_requests.put(request)
        else:
            logging.warning(f"Ignoring unknown lifecycle request: {request!r}")

    # ------------------------------------------------------------------
    # Workers: blocking I/O only
    # ------------------------------------------------------------------

    def _detection_worker(self):
        while not self._closing.is_set():
            try:
                generation = self.supervisor.generation
                snapshot = self.process_manager.observe()
                self._observations.put(ProcessObservation(generation, snapshot))
            except Exception:
                logging.exception("Process detection failed")
            self._closing.wait(settings.pollingRate)

    def _poll_worker(self):
        while not self._closing.is_set():
            request = self._poll_requests.get()
            if request is _SHUTDOWN:
                break
            try:
                players = self.client.get_player_names()
            except Exception:
                logging.exception("REST poll failed")
                players = None
            self._observations.put(
                RosterSample(request.generation, request.identity, request.seq, players)
            )

    def _process_worker(self):
        # Serialize process changes, including a restart after an observed exit
        # while the previous stop command is still returning.
        while not self._closing.is_set():
            request = self._process_requests.get()
            if request is _SHUTDOWN:
                break

            if self._closing.is_set():
                self._process_requests.put(request)
                break

            if isinstance(request, LaunchRequest):
                try:
                    identity = self.process_manager.launch_process(
                        settings.palworldServerExePath, settings.palworldExeArguments
                    )
                except Exception:
                    logging.exception("Server launch failed")
                    identity = None
                self._observations.put(LaunchResult(request.generation, identity))
            else:
                try:
                    stopped = self.process_manager.terminate_process(request.identity)
                except Exception:
                    logging.exception("Server termination failed")
                    stopped = False
                self._observations.put(
                    StopResult(request.generation, request.identity, stopped)
                )
