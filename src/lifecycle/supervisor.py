"""Lifecycle supervisor: the only writer of lifecycle, roster and auto-stop state.

Message handling is synchronous. The runtime calls ``handle``/``on_tick`` only
from its single lifecycle thread; this module owns no threads or queues.
"""

import logging
import threading
import time
from typing import Optional

from src.banlist_manager import BanlistManager
from src.events import EventBus, Event
from src.lifecycle import auto_stop
from src.lifecycle.messages import (
    LaunchRequest,
    LaunchResult,
    LifecycleState,
    PollRequest,
    ProcessIdentity,
    ProcessObservation,
    ProcessSnapshot,
    RosterSample,
    StartCommand,
    StopCommand,
    StopRequest,
    StopResult,
)
from src.player_manager import PlayerManager
from src.settings import settings


class Supervisor:
    def __init__(
        self,
        process_manager,
        player_manager: PlayerManager,
        banlist_manager: BanlistManager,
        bus: EventBus,
        clock=None,
    ):
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

        self._auto_stop = auto_stop.AutoStopState()
        self._poll_due_at: Optional[float] = None
        self._poll_inflight = False
        self._poll_seq = 0

    # ------------------------------------------------------------------
    # Public read access
    # ------------------------------------------------------------------

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    @property
    def auto_stop_deadline(self) -> Optional[float]:
        with self._state_lock:
            return self._auto_stop.deadline

    def get_snapshot(self) -> dict:
        """One consistent view of lifecycle, roster and REST health."""
        with self._state_lock:
            return {
                "revision": self._revision,
                "state": self._state,
                "playerData": self._player_data,
                "playerCount": self.player_manager.get_player_count(),
                "players": self.player_manager.get_all_players(),
                "banned_players": list(self.banlist_manager.get_banned_players()),
            }

    # ------------------------------------------------------------------
    # Synchronous message handling (lifecycle thread only)
    # ------------------------------------------------------------------

    def handle(self, item) -> list:
        if isinstance(item, StartCommand):
            return self._handle_start_command(item)
        if isinstance(item, StopCommand):
            return self._handle_stop_command(item)
        if isinstance(item, LaunchResult):
            return self._handle_launch_result(item)
        if isinstance(item, StopResult):
            return self._handle_stop_result(item)
        if isinstance(item, ProcessObservation):
            return self._handle_process_observation(item)
        if isinstance(item, RosterSample):
            return self._handle_roster_sample(item)
        logging.warning(f"Ignoring unknown lifecycle observation: {item!r}")
        return []

    def next_timeout(self) -> Optional[float]:
        now = self._clock()
        with self._state_lock:
            if self._state != LifecycleState.RUNNING:
                return None
            deadlines = []
            if self._poll_due_at is not None and not self._poll_inflight:
                deadlines.append(self._poll_due_at)
            if self._auto_stop.deadline is not None:
                deadlines.append(self._auto_stop.deadline)
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - now)

    def on_tick(self) -> list:
        requests = []
        now = self._clock()
        with self._state_lock:
            state = self._state
            generation = self._generation
            identity = self._identity
            poll_due = self._poll_due_at
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
            auto_stop_due = self._auto_stop.deadline
        if should_poll:
            requests.append(PollRequest(generation, identity, seq))
        if auto_stop_due is not None and now >= auto_stop_due:
            requests.extend(self._expire_auto_stop(generation))
        return requests

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _handle_start_command(self, command: StartCommand) -> list:
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
                self._auto_stop = auto_stop.AutoStopState()
                self._poll_due_at = None
                self._poll_inflight = False
                command.result = True
        # The command is accepted before the listener is asked to release the
        # port, so the auto-start thread never waits on this command.
        command.done.set()
        if generation is None:
            return []
        self._publish_lifecycle(LifecycleState.STARTING, generation)
        return [LaunchRequest(generation)]

    def _handle_stop_command(self, command: StopCommand) -> list:
        with self._state_lock:
            if self._state != LifecycleState.RUNNING:
                logging.warning(
                    "An attempt to stop the Palworld server was made, but it was not running."
                )
                command.result = False
                request = None
            elif self._clock() - self.last_server_stopped_time < (
                self.server_stopping_cooldown
            ):
                logging.warning(
                    "You attempted to restart the server too quickly after stopping it."
                )
                command.result = False
                request = None
            else:
                request = self._begin_stop_locked()
                command.result = request is not None
        if request is not None:
            self._publish_lifecycle(LifecycleState.STOPPING, request.generation)
        command.done.set()
        return [request] if request is not None else []

    def _begin_stop_locked(self) -> Optional[StopRequest]:
        identity = self._identity
        if identity is None:
            return None
        self._state = LifecycleState.STOPPING
        self._auto_stop = auto_stop.cancelled(self._auto_stop)
        self._poll_due_at = None
        self._poll_inflight = False
        return StopRequest(self._generation, identity)

    def _begin_stop(self) -> list:
        with self._state_lock:
            request = self._begin_stop_locked()
        if request is None:
            return []
        self._publish_lifecycle(LifecycleState.STOPPING, request.generation)
        return [request]

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def _handle_launch_result(self, result: LaunchResult) -> list:
        with self._state_lock:
            if (
                result.generation != self._generation
                or self._state != LifecycleState.STARTING
            ):
                return []
        if result.identity is None:
            self._commit_down()
        else:
            self._commit_started(result.identity)
        return []

    def _handle_stop_result(self, result: StopResult) -> list:
        with self._state_lock:
            if (
                result.generation != self._generation
                or self._state != LifecycleState.STOPPING
            ):
                return []
            outcome = result.stopped
        if outcome is True:
            self._commit_down()
        elif outcome is False:
            self._recover_from_failed_stop()
        # None means an LGSM shutdown is pending. Detection confirms the exit.
        return []

    def _recover_from_failed_stop(self):
        with self._state_lock:
            if self._state != LifecycleState.STOPPING:
                return
            self._state = LifecycleState.RUNNING
            self._poll_due_at = self._clock()
            self._poll_inflight = False
            generation = self._generation
        logging.warning(
            "Stop failed while the process is still alive; resuming polling"
        )
        self._publish_lifecycle(LifecycleState.RUNNING, generation)

    def _handle_process_observation(self, observation: ProcessObservation) -> list:
        with self._state_lock:
            if observation.generation != self._generation:
                return []
        return self._handle_process_snapshot(observation.snapshot)

    def _handle_process_snapshot(self, snapshot: ProcessSnapshot) -> list:
        with self._state_lock:
            state = self._state
        if state == LifecycleState.STARTING:
            return []
        if state == LifecycleState.STOPPED:
            return self._handle_stopped_snapshot(snapshot)
        return self._handle_active_snapshot(snapshot)

    def _handle_stopped_snapshot(self, snapshot: ProcessSnapshot) -> list:
        replacement = None
        stale_tracking = None
        reconcile = False
        with self._state_lock:
            if self._state != LifecycleState.STOPPED:
                return []
            if snapshot.tracked is not None:
                if snapshot.found is not None and snapshot.found.matches(
                    snapshot.tracked
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
        if stale_tracking is not None:
            self.process_manager.clear_identity(stale_tracking)
        if replacement is not None:
            self._adopt(replacement)
        elif reconcile:
            self._reconcile_stopped()
        return []

    def _handle_active_snapshot(self, snapshot: ProcessSnapshot) -> list:
        replacement = None
        with self._state_lock:
            current = self._identity
            if current is None:
                return []
            if snapshot.tracked is None or not snapshot.tracked.matches(current):
                return []  # stale observation from an older generation
            if snapshot.tracked_running:
                return []
            if snapshot.found is not None and not snapshot.found.matches(current):
                replacement = snapshot.found
        self._commit_down()
        if replacement is not None:
            self._adopt(replacement)
        return []

    def _adopt(self, identity: ProcessIdentity):
        self.process_manager.set_known_identity(identity)
        self._commit_started(identity)

    def _handle_roster_sample(self, sample: RosterSample) -> list:
        stop_now = False
        with self._state_lock:
            if sample.generation != self._generation:
                return []  # late response from a previous generation
            if self._state != LifecycleState.RUNNING:
                return []
            if self._identity is None or not self._identity.matches(sample.identity):
                return []
            self._poll_inflight = False
            self._poll_due_at = self._clock() + self.poll_interval
            generation = self._generation
            if sample.players is None:
                # REST is unknown/unavailable: keep the last-known roster and
                # never let an empty-looking failure trigger auto-stop.
                self._player_data = "unavailable"
                self._auto_stop, _decision = auto_stop.on_unavailable(self._auto_stop)
            else:
                players = list(sample.players)
                self._player_data = "ok"
                self.player_manager.update_players_from_server(players)
                self._auto_stop, decision = auto_stop.on_roster(
                    self._auto_stop,
                    config=self._auto_stop_config(),
                    now=self._clock(),
                    generation=generation,
                    player_count=len(players),
                    sample_seq=sample.seq,
                    started_at=self.last_server_started_time,
                )
                if decision == auto_stop.AutoStopDecision.STOP:
                    stop_now = True
                elif decision == auto_stop.AutoStopDecision.REVALIDATE:
                    self._poll_due_at = self._clock()
        self._publish_status(LifecycleState.RUNNING, generation)
        if stop_now:
            return self._begin_stop()
        return []

    # ------------------------------------------------------------------
    # State commits
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

    def _publish_status(self, state: str, generation: int):
        with self._state_lock:
            revision = self._bump_revision_locked()
            payload = {
                "revision": revision,
                "state": state,
                "playerData": self._player_data,
                "playerCount": self.player_manager.get_player_count(),
                "banned_players": list(self.banlist_manager.get_banned_players()),
                "generation": generation,
            }
        self.bus.publish(Event.SERVER_STATUS, payload)

    def _reconcile_stopped(self):
        """Make the persisted roster consistent with a stopped server."""
        with self._state_lock:
            if self._state != LifecycleState.STOPPED or self._stopped_reconciled:
                return
            self.player_manager.mark_all_offline()
            self._stopped_reconciled = True
            generation = self._generation
        self._publish_status(LifecycleState.STOPPED, generation)

    def _commit_started(self, identity: ProcessIdentity):
        with self._state_lock:
            if self._state != LifecycleState.STARTING:
                self._generation += 1
            self._state = LifecycleState.RUNNING
            self._identity = identity
            self._player_data = "unknown"
            self._stopped_reconciled = False
            self._auto_stop = auto_stop.AutoStopState()
            self.last_server_started_time = self._clock()
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
            self._auto_stop = auto_stop.AutoStopState()
            self.last_server_stopped_time = self._clock()
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
    # Discovery and auto-stop helpers
    # ------------------------------------------------------------------

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

    def _auto_stop_config(self) -> auto_stop.AutoStopConfig:
        return auto_stop.AutoStopConfig(
            enabled=settings.autoStop,
            delay=settings.autoStopDelay,
        )

    def _expire_auto_stop(self, generation: int) -> list:
        stop_now = False
        revalidate = False
        with self._state_lock:
            if self._state != LifecycleState.RUNNING or self._generation != generation:
                self._auto_stop = auto_stop.cancelled(self._auto_stop)
                return []
            self._auto_stop, decision = auto_stop.on_expiry(
                self._auto_stop,
                config=self._auto_stop_config(),
                now=self._clock(),
                generation=generation,
                poll_inflight=self._poll_inflight,
                poll_due_at=self._poll_due_at,
                poll_seq=self._poll_seq,
                poll_interval=self.poll_interval,
            )
            if decision == auto_stop.AutoStopDecision.REVALIDATE:
                revalidate = True
                if not self._poll_inflight:
                    self._poll_due_at = self._clock()
            elif decision == auto_stop.AutoStopDecision.STOP:
                stop_now = True
        if stop_now:
            logging.info("Auto-stop delay elapsed with a fresh empty sample")
            return self._begin_stop()
        if revalidate:
            logging.info("Auto-stop revalidation requested")
        return []
