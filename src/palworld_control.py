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

"""Single-writer lifecycle supervisor for the Palworld server.

Workers do blocking I/O (process scans, REST calls, launches, terminations)
and submit observations to one queue. The supervisor is the only writer of
lifecycle, roster and auto-stop state; it commits a transition and only then
publishes the corresponding notification.
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

from src.banlist_manager import BanlistManager
from src.events import EventBus, Event
from src.player_manager import PlayerManager
from src.process_manager import ProcessIdentity, ProcessSnapshot
from src.settings import settings


class LifecycleState:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"


AUTO_STOP_STARTUP_GUARD = 30


@dataclass(frozen=True)
class ProcessObservation:
    """A process scan tagged with the generation it started under."""

    generation: int
    snapshot: ProcessSnapshot


@dataclass(frozen=True)
class LaunchRequest:
    generation: int


@dataclass(frozen=True)
class LaunchResult:
    generation: int
    identity: Optional[ProcessIdentity]


@dataclass(frozen=True)
class PollRequest:
    generation: int
    identity: ProcessIdentity
    seq: int


@dataclass(frozen=True)
class RosterSample:
    generation: int
    identity: ProcessIdentity
    seq: int
    players: Optional[list]  # None means the REST API was unavailable.


@dataclass(frozen=True)
class StopRequest:
    generation: int
    identity: ProcessIdentity


@dataclass(frozen=True)
class StopResult:
    generation: int
    identity: Optional[ProcessIdentity]
    stopped: Optional[bool]  # True=gone, False=failed, None=pending shutdown


@dataclass(frozen=True)
class ProcessExit:
    identity: ProcessIdentity


class _Command:
    def __init__(self):
        self.done = threading.Event()
        self.result = False


class _StartCommand(_Command):
    pass


class _StopCommand(_Command):
    pass


_SHUTDOWN = object()
_WORKER_JOIN_TIMEOUT = 5


class PalWorldController:
    def __init__(
        self,
        client,
        process_manager,
        player_manager: PlayerManager,
        banlist_manager: BanlistManager,
        bus: EventBus,
        clock=None,
    ):
        self.client = client
        self.process_manager = process_manager
        self.player_manager = player_manager
        self.banlist_manager = banlist_manager
        self.bus = bus
        self._clock = clock or time.monotonic

        self.server_starting_cooldown = 5
        self.server_stopping_cooldown = 5
        self.poll_interval = settings.pollingRate
        self.last_server_started_time = 0.0
        self.last_server_stopped_time = 0.0

        self._state = LifecycleState.STOPPED
        self._identity: Optional[ProcessIdentity] = None
        self._generation = 0
        self._revision = 0
        self._state_lock = threading.RLock()
        self._player_data = "unknown"  # unknown | ok | unavailable
        self._stopped_reconciled = False

        self._auto_stop_deadline: Optional[float] = None
        self._auto_stop_generation: Optional[int] = None
        self._auto_stop_revalidation: Optional[int] = None
        self._auto_stop_revalidation_seq: Optional[int] = None
        self._last_valid_sample_at: Optional[float] = None
        self._last_valid_sample_generation: Optional[int] = None
        self._last_valid_roster_empty = False
        self._poll_due_at: Optional[float] = None
        self._poll_inflight = False
        self._poll_seq = 0

        self._observations: queue.Queue = queue.Queue()
        self._poll_requests: queue.Queue = queue.Queue()
        self._launch_requests: queue.Queue = queue.Queue()
        self._stop_requests: queue.Queue = queue.Queue()

        self._closing = threading.Event()
        self._started = False
        self._start_lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

        set_callback = getattr(self.process_manager, "set_exit_callback", None)
        if set_callback is not None:
            set_callback(self._on_process_exit)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the supervisor and its workers. Idempotent."""
        with self._start_lock:
            if self._started:
                return
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
                "palworld-launch": threading.Thread(
                    target=self._launch_worker, name="palworld-launch", daemon=True
                ),
                "palworld-stop": threading.Thread(
                    target=self._stop_worker, name="palworld-stop", daemon=True
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
            for requests in (
                self._poll_requests,
                self._launch_requests,
                self._stop_requests,
            ):
                requests.put(None)
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

    def start_server(self):
        logging.info("Palworld server start command received")
        return self._submit_command(_StartCommand())

    def stop_server(self):
        logging.info("Palworld server stop command received")
        return self._submit_command(_StopCommand())

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def get_snapshot(self) -> dict:
        """One consistent view of lifecycle, roster and REST health."""
        with self._state_lock:
            players = self.player_manager.get_all_players()
            return {
                "revision": self._revision,
                "state": self._state,
                "playerData": self._player_data,
                "playerCount": sum(
                    1 for player in players if player.get("currently_online")
                ),
                "players": players,
                "banned_players": list(self.banlist_manager.get_banned_players()),
            }

    def get_banned_players(self):
        return self.banlist_manager.get_banned_players()

    def is_player_banned(self, steam_id):
        return self.banlist_manager.is_banned(steam_id)

    def kick_player(self, steam_id):
        try:
            return self.client.kick_player({"steam_id": steam_id})
        except Exception as e:
            logging.error(f"Error kicking {steam_id}: {e}")
            return False

    def ban_player(self, steam_id):
        try:
            success = self.client.ban_player({"steam_id": steam_id})
            if success:
                return self.banlist_manager.add_ban(steam_id)

            return success
        except Exception as e:
            logging.error(f"Error banning {steam_id}: {e}")
            return False

    def unban_player(self, steam_id):
        try:
            success = self.client.unban_player({"steam_id": steam_id})
            if success:
                return self.banlist_manager.remove_ban(steam_id)

            return success
        except Exception as e:
            logging.error(f"Error unbanning {steam_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Supervisor loop
    # ------------------------------------------------------------------

    def _submit_command(self, command: _Command) -> bool:
        if not self._started or self._closing.is_set():
            logging.warning("Controller is not running; command ignored")
            return False
        self._observations.put(command)
        if not command.done.wait(timeout=5):
            logging.error("Lifecycle command timed out")
            return False
        return command.result

    def _supervisor_loop(self):
        while not self._closing.is_set():
            timeout = self._next_timeout()
            try:
                item = self._observations.get(timeout=timeout)
            except queue.Empty:
                self._on_tick()
                continue
            if item is _SHUTDOWN:
                break
            try:
                self._handle(item)
            except Exception:
                logging.exception(
                    "Lifecycle supervisor failed to handle an observation"
                )

    def _next_timeout(self) -> Optional[float]:
        now = self._clock()
        with self._state_lock:
            if self._state != LifecycleState.RUNNING:
                return None
            deadlines = []
            if self._poll_due_at is not None and not self._poll_inflight:
                deadlines.append(self._poll_due_at)
            if self._auto_stop_deadline is not None:
                deadlines.append(self._auto_stop_deadline)
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - now)

    def _on_tick(self):
        now = self._clock()
        with self._state_lock:
            state = self._state
            generation = self._generation
            identity = self._identity
            poll_due = self._poll_due_at
            auto_stop_due = self._auto_stop_deadline
            should_poll = (
                state == LifecycleState.RUNNING
                and identity is not None
                and not self._poll_inflight
                and poll_due is not None
                and now >= poll_due
            )
            if should_poll:
                self._poll_inflight = True
                self._poll_due_at = None
                self._poll_seq += 1
                seq = self._poll_seq
        if should_poll:
            self._poll_requests.put(PollRequest(generation, identity, seq))
        if auto_stop_due is not None and now >= auto_stop_due:
            self._expire_auto_stop(generation)

    def _handle(self, item):
        if isinstance(item, _StartCommand):
            self._handle_start_command(item)
        elif isinstance(item, _StopCommand):
            self._handle_stop_command(item)
        elif isinstance(item, LaunchResult):
            self._handle_launch_result(item)
        elif isinstance(item, StopResult):
            self._handle_stop_result(item)
        elif isinstance(item, ProcessExit):
            self._handle_process_exit(item)
        elif isinstance(item, ProcessObservation):
            self._handle_process_observation(item)
        elif isinstance(item, RosterSample):
            self._handle_roster_sample(item)
        else:
            logging.warning(f"Ignoring unknown lifecycle observation: {item!r}")

    def _has_live_tracked_process(self) -> bool:
        tracked = self.process_manager.tracked_identity
        if tracked is not None and self.process_manager.identity_is_running(tracked):
            return True
        # No live tracked identity is not proof that no server exists: the
        # first detection scan may not have adopted it yet.
        if self.process_manager.find_process_identity("PalServer") is not None:
            return True
        if tracked is not None:
            self.process_manager.clear_identity(tracked)
        return False

    def _handle_start_command(self, command: _StartCommand):
        generation = None
        with self._state_lock:
            if self._state != LifecycleState.STOPPED:
                logging.warning("Palworld Server is already running or starting.")
                command.result = False
            elif self._has_live_tracked_process():
                logging.warning("A Palworld server process is already tracked.")
                command.result = False
            elif self._clock() - self.last_server_started_time < (
                self.server_starting_cooldown
            ):
                logging.warning("Tried to start the server too quickly multiple times.")
                command.result = False
            elif self._clock() - self.last_server_stopped_time < (
                self.server_stopping_cooldown
            ):
                logging.warning(
                    "You attempted to restart the server too quickly after stopping it."
                )
                command.result = False
            else:
                self._generation += 1
                generation = self._generation
                self._state = LifecycleState.STARTING
                self._identity = None
                self._player_data = "unknown"
                self._cancel_auto_stop_locked()
                self._poll_due_at = None
                self._poll_inflight = False
                command.result = True
        # The command is accepted before the listener is asked to release the
        # port, so the auto-start thread never waits on this command.
        command.done.set()
        if generation is not None:
            self._publish_lifecycle(LifecycleState.STARTING, generation)
            self._launch_requests.put(LaunchRequest(generation))

    def _handle_stop_command(self, command: _StopCommand):
        generation = None
        with self._state_lock:
            if self._state != LifecycleState.RUNNING:
                logging.warning(
                    "An attempt to stop the Palworld server was made, but it was not running."
                )
                command.result = False
            elif self._clock() - self.last_server_stopped_time < (
                self.server_stopping_cooldown
            ):
                logging.warning(
                    "You attempted to restart the server too quickly after stopping it."
                )
                command.result = False
            else:
                command.result = self._begin_stop_locked()
                if command.result:
                    generation = self._generation
        if generation is not None:
            self._publish_lifecycle(LifecycleState.STOPPING, generation)
        command.done.set()

    def _begin_stop_locked(self) -> bool:
        identity = self._identity
        if identity is None:
            return False
        self._state = LifecycleState.STOPPING
        self._cancel_auto_stop_locked()
        self._poll_due_at = None
        self._poll_inflight = False
        self._stop_requests.put(StopRequest(self._generation, identity))
        return True

    def _handle_launch_result(self, result: LaunchResult):
        with self._state_lock:
            if (
                result.generation != self._generation
                or self._state != LifecycleState.STARTING
            ):
                return
        if result.identity is None:
            self._commit_down()
        else:
            self._commit_started(result.identity)

    def _handle_stop_result(self, result: StopResult):
        with self._state_lock:
            if (
                result.generation != self._generation
                or self._state != LifecycleState.STOPPING
            ):
                return
            outcome = result.stopped
        if outcome is True:
            self._commit_down()
        elif outcome is False:
            self._recover_from_failed_stop()
        # None means the process manager is still watching an asynchronous
        # shutdown (LGSM); stay STOPPING until it reports an exit.

    def _recover_from_failed_stop(self):
        with self._state_lock:
            if self._state != LifecycleState.STOPPING:
                return
            self._state = LifecycleState.RUNNING
            self._poll_due_at = self._clock()
            self._poll_inflight = False
            generation = self._generation
        logging.warning("Stop failed while the process is still alive; resuming polling")
        self._publish_lifecycle(LifecycleState.RUNNING, generation)

    def _handle_process_exit(self, observation: ProcessExit):
        with self._state_lock:
            if self._identity is None or not self._identity.matches(
                observation.identity
            ):
                return
            if self._state not in (LifecycleState.RUNNING, LifecycleState.STOPPING):
                return
        self._commit_down()

    def _handle_process_observation(self, observation: ProcessObservation):
        with self._state_lock:
            if observation.generation != self._generation:
                return
        self._handle_process_snapshot(observation.snapshot)

    def _handle_process_snapshot(self, snapshot: ProcessSnapshot):
        replacement = None
        stale_tracking = None
        reconcile = False
        with self._state_lock:
            state = self._state
            current = self._identity
            if state == LifecycleState.STARTING:
                return
            if state == LifecycleState.STOPPED:
                if snapshot.tracked is not None:
                    if (
                        snapshot.found is not None
                        and snapshot.found.matches(snapshot.tracked)
                    ):
                        # Only adopt a tracked PID when this scan actually
                        # found it as PalServer; mere PID liveness is not proof.
                        replacement = snapshot.tracked
                    else:
                        stale_tracking = snapshot.tracked
                if replacement is None and snapshot.found is not None:
                    replacement = snapshot.found
                if replacement is None and not self._stopped_reconciled:
                    reconcile = True
            else:
                if current is None:
                    return
                if snapshot.tracked is None or not snapshot.tracked.matches(current):
                    return  # stale observation from an older generation
                if snapshot.tracked_running:
                    return
                if snapshot.found is not None and not snapshot.found.matches(current):
                    replacement = snapshot.found
        if stale_tracking is not None:
            self.process_manager.clear_identity(stale_tracking)
        if state == LifecycleState.STOPPED:
            if replacement is not None:
                self._adopt(replacement)
            elif reconcile:
                self._reconcile_stopped()
            return
        self._commit_down()
        if replacement is not None:
            self._adopt(replacement)

    def _adopt(self, identity: ProcessIdentity):
        self.process_manager.set_known_identity(identity)
        self._commit_started(identity)

    def _handle_roster_sample(self, sample: RosterSample):
        stop_now = False
        with self._state_lock:
            if sample.generation != self._generation:
                return  # late response from a previous generation
            if self._state != LifecycleState.RUNNING:
                return
            if self._identity is None or not self._identity.matches(sample.identity):
                return
            self._poll_inflight = False
            self._poll_due_at = self._clock() + self.poll_interval
            generation = self._generation
            revalidating = self._auto_stop_revalidation == generation
            if sample.players is None:
                # REST is unknown/unavailable: keep the last-known roster and
                # never let an empty-looking failure trigger auto-stop.
                self._player_data = "unavailable"
                self._last_valid_sample_at = None
                self._last_valid_sample_generation = None
                self._last_valid_roster_empty = False
                self._cancel_auto_stop_locked()
            else:
                players = list(sample.players)
                self._player_data = "ok"
                self.player_manager.update_players_from_server(players)
                self._last_valid_sample_at = self._clock()
                self._last_valid_sample_generation = generation
                self._last_valid_roster_empty = not players
                if revalidating:
                    if players:
                        self._cancel_auto_stop_locked()
                    elif sample.seq >= (self._auto_stop_revalidation_seq or 0):
                        self._auto_stop_revalidation = None
                        self._auto_stop_revalidation_seq = None
                        stop_now = True
                    else:
                        # A poll captured before the revalidation request
                        # cannot confirm it; ask for a newer sample.
                        self._poll_due_at = self._clock()
                else:
                    self._update_auto_stop_locked(len(players))
            banned_players = list(self.banlist_manager.get_banned_players())
            players_snapshot = self.player_manager.get_all_players()
            player_count = sum(
                1 for player in players_snapshot if player.get("currently_online")
            )
            player_data = self._player_data
            revision = self._bump_revision_locked()
        self.bus.publish(
            Event.SERVER_STATUS,
            {
                "revision": revision,
                "state": LifecycleState.RUNNING,
                "playerData": player_data,
                "playerCount": player_count,
                "banned_players": banned_players,
                "generation": generation,
            },
        )
        if stop_now:
            self._begin_stop()

    # ------------------------------------------------------------------
    # State commits (supervisor only)
    # ------------------------------------------------------------------

    def _bump_revision_locked(self) -> int:
        # Monotonic revision for ordered web/SSE snapshots.
        self._revision += 1
        return self._revision

    def _publish_lifecycle(self, state: str, generation: int):
        with self._state_lock:
            revision = self._bump_revision_locked()
        self.bus.publish(
            Event.LIFECYCLE_CHANGED,
            {"state": state, "generation": generation, "revision": revision},
        )

    def _reconcile_stopped(self):
        """Make the persisted roster consistent with a stopped server."""
        with self._state_lock:
            if self._state != LifecycleState.STOPPED or self._stopped_reconciled:
                return
            self.player_manager.mark_all_offline()
            self._stopped_reconciled = True
            revision = self._bump_revision_locked()
            generation = self._generation
            players = self.player_manager.get_all_players()
            player_count = sum(
                1 for player in players if player.get("currently_online")
            )
            player_data = self._player_data
            banned_players = list(self.banlist_manager.get_banned_players())
        self.bus.publish(
            Event.SERVER_STATUS,
            {
                "revision": revision,
                "state": LifecycleState.STOPPED,
                "playerData": player_data,
                "playerCount": player_count,
                "banned_players": banned_players,
                "generation": generation,
            },
        )

    def _commit_started(self, identity: ProcessIdentity):
        with self._state_lock:
            if self._state != LifecycleState.STARTING:
                self._generation += 1
            self._state = LifecycleState.RUNNING
            self._identity = identity
            self._player_data = "unknown"
            self._stopped_reconciled = False
            self._last_valid_sample_at = None
            self._last_valid_sample_generation = None
            self._last_valid_roster_empty = False
            self.last_server_started_time = self._clock()
            self._cancel_auto_stop_locked()
            self._poll_due_at = self._clock()
            self._poll_inflight = False
            generation = self._generation
            revision = self._bump_revision_locked()
        self.bus.publish(
            Event.SERVER_STARTED,
            {
                "revision": revision,
                "pid": identity.pid,
                "generation": generation,
                "state": LifecycleState.RUNNING,
            },
        )

    def _commit_down(self):
        with self._state_lock:
            stopped = self._identity
            self._state = LifecycleState.STOPPED
            self._identity = None
            self._generation += 1
            self._player_data = "unknown"
            self._stopped_reconciled = True
            self._last_valid_sample_at = None
            self._last_valid_sample_generation = None
            self._last_valid_roster_empty = False
            self.last_server_stopped_time = self._clock()
            self._cancel_auto_stop_locked()
            self._poll_due_at = None
            self._poll_inflight = False
            self.player_manager.mark_all_offline()
            generation = self._generation
            revision = self._bump_revision_locked()
        if stopped is not None:
            self.process_manager.clear_identity(stopped)
        self.bus.publish(
            Event.SERVER_STOPPED,
            {
                "revision": revision,
                "pid": stopped.pid if stopped is not None else None,
                "generation": generation,
                "state": LifecycleState.STOPPED,
            },
        )

    # ------------------------------------------------------------------
    # Auto-stop deadline (supervisor owned)
    # ------------------------------------------------------------------

    def _cancel_auto_stop_locked(self):
        self._auto_stop_deadline = None
        self._auto_stop_generation = None
        self._auto_stop_revalidation = None
        self._auto_stop_revalidation_seq = None

    def _update_auto_stop_locked(self, player_count: int):
        if not settings.autoStop or player_count > 0:
            self._cancel_auto_stop_locked()
            return
        now = self._clock()
        if now - self.last_server_started_time < AUTO_STOP_STARTUP_GUARD:
            self._cancel_auto_stop_locked()
            return
        if (
            self._auto_stop_deadline is None
            or self._auto_stop_generation != self._generation
        ):
            self._auto_stop_deadline = now + settings.autoStopDelay
            self._auto_stop_generation = self._generation
            logging.info(
                f"Auto-stop armed, stopping in {settings.autoStopDelay}s if still empty"
            )

    def _sample_staleness_limit(self) -> float:
        return max(self.poll_interval * 2, 1.0)

    def _has_fresh_empty_sample_locked(self, generation: int, now: float) -> bool:
        return (
            self._last_valid_sample_generation == generation
            and self._last_valid_sample_at is not None
            and now - self._last_valid_sample_at <= self._sample_staleness_limit()
            and self._last_valid_roster_empty
            and not self._poll_inflight
            and (self._poll_due_at is None or self._poll_due_at > now)
        )

    def _expire_auto_stop(self, generation: int):
        stop_now = False
        request_sample = False
        with self._state_lock:
            if (
                self._state != LifecycleState.RUNNING
                or self._generation != generation
                or self._auto_stop_generation != generation
            ):
                self._cancel_auto_stop_locked()
                return
            if self._auto_stop_revalidation == generation:
                return
            now = self._clock()
            if self._has_fresh_empty_sample_locked(generation, now):
                self._auto_stop_deadline = None
                self._auto_stop_generation = None
                stop_now = True
            else:
                # The last sample is stale, unavailable, or a poll is due or
                # in flight. Never stop on old data: wait for a fresh sample.
                self._auto_stop_revalidation = generation
                self._auto_stop_revalidation_seq = self._poll_seq + 1
                self._auto_stop_deadline = None
                self._auto_stop_generation = None
                if not self._poll_inflight:
                    self._poll_due_at = now
                    request_sample = True
        if stop_now:
            logging.info("Auto-stop delay elapsed with a fresh empty sample")
            self._begin_stop()
        elif request_sample:
            logging.info("Auto-stop revalidation requested")

    def _begin_stop(self) -> bool:
        with self._state_lock:
            if not self._begin_stop_locked():
                return False
            generation = self._generation
        self._publish_lifecycle(LifecycleState.STOPPING, generation)
        return True

    # ------------------------------------------------------------------
    # Workers: blocking I/O only, results submitted as observations
    # ------------------------------------------------------------------

    def _on_process_exit(self, identity: ProcessIdentity):
        self._observations.put(ProcessExit(identity))

    def _detection_worker(self):
        while not self._closing.is_set():
            try:
                generation = self.generation
                snapshot = self.process_manager.observe()
                self._observations.put(ProcessObservation(generation, snapshot))
            except Exception:
                logging.exception("Process detection failed")
            self._closing.wait(settings.pollingRate)

    def _poll_worker(self):
        while not self._closing.is_set():
            request = self._poll_requests.get()
            if request is None:
                break
            try:
                players = self.client.get_player_names()
            except Exception:
                logging.exception("REST poll failed")
                players = None
            self._observations.put(
                RosterSample(
                    request.generation, request.identity, request.seq, players
                )
            )

    def _launch_worker(self):
        while not self._closing.is_set():
            request = self._launch_requests.get()
            if request is None:
                break
            if self._closing.is_set():
                break
            try:
                identity = self.process_manager.launch_process(
                    settings.palworldServerExePath, settings.palworldExeArguments
                )
            except Exception:
                logging.exception("Server launch failed")
                identity = None
            self._observations.put(LaunchResult(request.generation, identity))

    def _stop_worker(self):
        while not self._closing.is_set():
            request = self._stop_requests.get()
            if request is None:
                break
            try:
                stopped = self.process_manager.terminate_process(request.identity)
            except Exception:
                logging.exception("Server termination failed")
                stopped = False
            self._observations.put(
                StopResult(request.generation, request.identity, stopped)
            )
