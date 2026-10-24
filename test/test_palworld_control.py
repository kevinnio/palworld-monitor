"""Integration and facade tests for PalWorldController."""

import threading
import time

import pytest

from unittest.mock import MagicMock

from src.banlist_manager import BanlistManager
from src.events import Event, EventBus
from src.lifecycle.messages import LifecycleState, ProcessIdentity, ProcessSnapshot
from src.palworld_control import PalWorldController
from src.player_manager import PlayerManager


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeProcessManager:
    def __init__(self):
        self.identity = None
        self.alive = set()
        self.found = None
        self.launch_identity = None
        self.launch_block = None
        self.terminate_block = None
        self.terminate_entered = threading.Event()
        self.terminate_result = True
        self.terminate_mutates = True
        self.launch_calls = []
        self.terminate_calls = []
        self.closed = False
        self.observe_gate = None
        self.observe_entered = threading.Event()
        self.gated_snapshot = None

    @property
    def tracked_identity(self):
        return self.identity

    def set_known_identity(self, identity):
        self.identity = identity

    def clear_identity(self, expected):
        if self.identity is None or expected is None:
            return False
        if not self.identity.matches(expected):
            return False
        self.identity = None
        return True

    def identity_is_running(self, identity):
        return identity is not None and identity.pid in self.alive

    def find_process_identity(self, name):
        return self.found

    def observe(self):
        if self.observe_gate is not None:
            gate = self.observe_gate
            self.observe_gate = None
            self.observe_entered.set()
            gate.wait(5)
            return self.gated_snapshot
        return ProcessSnapshot(
            tracked=self.identity,
            tracked_running=self.identity_is_running(self.identity),
            found=self.found,
        )

    def launch_process(self, exe_path, exe_args):
        self.launch_calls.append((exe_path, exe_args))
        if self.launch_block is not None:
            self.launch_block.wait(5)
        if self.launch_identity is None:
            return None
        self.identity = self.launch_identity
        self.alive.add(self.identity.pid)
        self.found = self.identity
        return self.identity

    def terminate_process(self, expected):
        self.terminate_calls.append(expected)
        if self.terminate_block is not None:
            self.terminate_entered.set()
            self.terminate_block.wait(5)
        if self.terminate_result is not True:
            return self.terminate_result
        if self.terminate_mutates:
            if self.identity is None or not self.identity.matches(expected):
                return False
            self.alive.discard(self.identity.pid)
            self.clear_identity(self.identity)
            self.found = None
        return True

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.players = []
        self.available = True
        self.block = None
        self.entered = threading.Event()
        self.calls = 0

    def get_player_names(self):
        self.calls += 1
        self.entered.set()
        players = [list(player) for player in self.players]
        if self.block is not None:
            self.block.wait(5)
        if not self.available:
            return None
        return players

    def kick_player(self, player):
        return True

    def ban_player(self, player):
        return True

    def unban_player(self, player):
        return True


class Harness:
    def __init__(self, bus, client, process_manager, players, bans, clock, controller):
        self.bus = bus
        self.client = client
        self.pm = process_manager
        self.players = players
        self.bans = bans
        self.clock = clock
        self.controller = controller
        self.events = []
        bus.subscribe(
            Event.SERVER_STARTED,
            lambda data: self.events.append((Event.SERVER_STARTED, data)),
        )
        bus.subscribe(
            Event.SERVER_STOPPED,
            lambda data: self.events.append((Event.SERVER_STOPPED, data)),
        )
        bus.subscribe(
            Event.SERVER_STATUS,
            lambda data: self.events.append((Event.SERVER_STATUS, data)),
        )
        bus.subscribe(
            Event.LIFECYCLE_CHANGED,
            lambda data: self.events.append((Event.LIFECYCLE_CHANGED, data)),
        )

    def start(self):
        self.controller.start()

    def close(self):
        self.controller.close()

    def start_with_process(self, identity=None):
        identity = identity or ProcessIdentity(1001, 10.0)
        self.pm.found = identity
        self.pm.alive.add(identity.pid)
        self.start()
        wait_until(lambda: self.controller.state == LifecycleState.RUNNING)
        return identity

    def events_of(self, event_type):
        return [data for kind, data in self.events if kind == event_type]


CONTROLLER_THREAD_NAMES = {
    "palworld-lifecycle",
    "palworld-detection",
    "palworld-poll",
    "palworld-process",
}


def wait_until(predicate, timeout=3.0, message="condition not met"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), message


@pytest.fixture
def harness(mock_settings, tmp_path):
    mock_settings.pollingRate = 0.01
    mock_settings.autoStop = False
    mock_settings.autoStopDelay = 60
    mock_settings.enablePlayerTracking = True

    bus = EventBus()
    client = FakeClient()
    process_manager = FakeProcessManager()
    players = PlayerManager(bus=bus, data_file=str(tmp_path / "players.json"))
    bans = BanlistManager(banlist_path=str(tmp_path / "banlist.txt"), bus=bus)
    clock = FakeClock()
    controller = PalWorldController(
        client=client,
        process_manager=process_manager,
        player_manager=players,
        banlist_manager=bans,
        bus=bus,
        clock=clock,
    )
    controller._supervisor.server_starting_cooldown = 0
    controller._supervisor.server_stopping_cooldown = 0
    controller._supervisor.poll_interval = 0

    h = Harness(bus, client, process_manager, players, bans, clock, controller)
    yield h
    h.close()


class TestPalWorldController:
    def test_constructor_does_not_start_workers(self, harness):
        assert not any(
            thread.name in CONTROLLER_THREAD_NAMES for thread in threading.enumerate()
        )
        assert harness.controller.start_server() is False

    def test_stale_pid_startup_reconciles_and_does_not_block_start(self, harness):
        harness.players.update_players_from_server([["P1", "uid1", "111", "10"]])
        assert harness.players.get_player_count() == 1
        harness.pm.identity = ProcessIdentity(999, 1.0)
        harness.pm.launch_identity = ProcessIdentity(2001, 20.0)
        harness.start()

        wait_until(lambda: harness.pm.tracked_identity is None)
        wait_until(lambda: harness.players.get_player_count() == 0)
        snapshot = harness.controller.get_snapshot()
        assert snapshot["state"] == LifecycleState.STOPPED
        assert snapshot["playerData"] == "unknown"
        assert all(not player["currently_online"] for player in snapshot["players"])

        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

    def test_stopped_startup_marks_persisted_online_players_offline(self, harness):
        harness.players.update_players_from_server([["P1", "uid1", "111", "10"]])
        harness.start()

        wait_until(lambda: harness.players.get_player_count() == 0)
        snapshot = harness.controller.get_snapshot()
        assert snapshot["state"] == LifecycleState.STOPPED
        assert snapshot["playerData"] == "unknown"
        assert [player["steam_id"] for player in snapshot["players"]] == ["111"]
        assert snapshot["players"][0]["currently_online"] is False

    def test_start_clears_a_stale_tracked_identity_before_detection(self, harness):
        harness.pm.identity = ProcessIdentity(999, 1.0)
        harness.pm.launch_identity = ProcessIdentity(2001, 20.0)
        harness.start()

        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)
        assert harness.pm.tracked_identity == ProcessIdentity(2001, 20.0)

    def test_failed_stop_returns_to_running_and_resumes_polling(self, harness):
        harness.client.players = [["P1", "uid1", "111", "10"]]
        harness.start_with_process()
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")

        harness.client.players = [["P2", "uid2", "222", "20"]]
        harness.pm.terminate_result = False
        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

        wait_until(
            lambda: any(
                player["steam_id"] == "222" and player["currently_online"]
                for player in harness.controller.get_snapshot()["players"]
            )
        )

        harness.pm.terminate_result = True
        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

    def test_pending_stop_stays_stopping_until_the_process_exits(self, harness):
        identity = harness.start_with_process()
        harness.pm.terminate_result = None

        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPING)
        time.sleep(0.1)
        assert harness.controller.state == LifecycleState.STOPPING

        harness.pm.alive.discard(identity.pid)
        harness.pm.found = None
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

    def test_blocked_rest_at_deadline_does_not_stop_on_an_old_empty_sample(
        self, harness
    ):
        from src.lifecycle.supervisor import settings as controller_settings

        controller_settings.autoStop = True
        harness.client.players = []
        harness.start_with_process()
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")

        # Pass the startup guard so a later empty sample arms the deadline.
        harness.clock.advance(31)
        wait_until(
            lambda: harness.controller._supervisor.auto_stop_deadline is not None
        )

        # A poll is already in flight when the deadline expires.
        harness.client.entered.clear()
        harness.client.block = threading.Event()
        wait_until(lambda: harness.client.entered.is_set())
        harness.clock.advance(controller_settings.autoStopDelay + 1)
        time.sleep(0.1)
        assert harness.controller.state == LifecycleState.RUNNING

        # The old blocked sample captured an empty roster. It must not stop
        # the server; a fresh non-empty sample cancels the revalidation.
        harness.client.players = [["P1", "uid1", "111", "10"]]
        harness.client.block.set()
        wait_until(
            lambda: any(
                player["steam_id"] == "111" and player["currently_online"]
                for player in harness.controller.get_snapshot()["players"]
            )
        )
        assert harness.controller.state == LifecycleState.RUNNING

    def test_auto_stop_waits_for_a_fresh_empty_sample_to_confirm(self, harness):
        from src.lifecycle.supervisor import settings as controller_settings

        controller_settings.autoStop = True
        harness.client.players = []
        harness.start_with_process()
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")
        harness.clock.advance(31)
        wait_until(
            lambda: harness.controller._supervisor.auto_stop_deadline is not None
        )

        harness.client.entered.clear()
        harness.client.block = threading.Event()
        wait_until(lambda: harness.client.entered.is_set())
        harness.clock.advance(controller_settings.autoStopDelay + 1)
        time.sleep(0.1)
        assert harness.controller.state == LifecycleState.RUNNING

        harness.client.block.set()
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

    def test_delayed_process_observation_cannot_adopt_after_down(self, harness):
        identity = harness.start_with_process()
        stale_found = ProcessIdentity(2002, 20.0)
        gate = threading.Event()
        harness.pm.gated_snapshot = ProcessSnapshot(
            tracked=identity, tracked_running=True, found=stale_found
        )
        harness.pm.observe_entered.clear()
        harness.pm.observe_gate = gate
        assert harness.pm.observe_entered.wait(1)

        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)
        started_before = len(harness.events_of(Event.SERVER_STARTED))

        gate.set()
        time.sleep(0.15)

        assert harness.controller.state == LifecycleState.STOPPED
        assert len(harness.events_of(Event.SERVER_STARTED)) == started_before

    def test_unadopted_found_process_blocks_a_start_until_detection_adopts(
        self, harness
    ):
        identity = ProcessIdentity(2002, 20.0)
        harness.pm.found = identity
        harness.pm.alive.add(identity.pid)
        harness.pm.gated_snapshot = ProcessSnapshot(
            tracked=None, tracked_running=False, found=identity
        )
        harness.pm.launch_identity = ProcessIdentity(3003, 30.0)
        gate = threading.Event()
        harness.pm.observe_entered.clear()
        harness.pm.observe_gate = gate
        harness.start()
        assert harness.pm.observe_entered.wait(1)
        assert harness.controller.state == LifecycleState.STOPPED
        assert harness.pm.tracked_identity is None

        assert harness.controller.start_server() is False
        assert harness.pm.launch_calls == []

        gate.set()
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)
        assert harness.pm.tracked_identity == identity
        assert harness.pm.launch_calls == []

    def test_reused_live_pid_without_a_palworld_scan_is_not_adopted(self, harness):
        reused = ProcessIdentity(999, 1.0)
        harness.pm.identity = reused
        harness.pm.alive.add(reused.pid)
        harness.pm.found = None
        harness.start()

        wait_until(lambda: harness.pm.tracked_identity is None)
        assert harness.controller.state == LifecycleState.STOPPED
        assert harness.events_of(Event.SERVER_STARTED) == []
        assert reused.pid in harness.pm.alive
        assert harness.pm.terminate_calls == []

        harness.pm.launch_identity = ProcessIdentity(2001, 20.0)
        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

    def test_reused_pid_is_replaced_by_the_actual_found_palworld(self, harness):
        reused = ProcessIdentity(999, 1.0)
        actual = ProcessIdentity(2002, 20.0)
        harness.pm.identity = reused
        harness.pm.alive.add(reused.pid)
        harness.pm.found = actual
        harness.pm.alive.add(actual.pid)
        harness.start()

        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)
        assert harness.pm.tracked_identity == actual
        assert reused.pid in harness.pm.alive
        assert harness.pm.terminate_calls == []
        assert harness.events_of(Event.SERVER_STARTED)[-1]["pid"] == actual.pid
        assert harness.events_of(Event.SERVER_STOPPED) == []

    def test_tracked_palworld_found_by_scan_is_adopted(self, harness):
        tracked = ProcessIdentity(2002, 20.0)
        harness.pm.identity = tracked
        harness.pm.alive.add(tracked.pid)
        harness.pm.found = tracked
        harness.start()

        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)
        assert harness.pm.tracked_identity == tracked
        assert harness.pm.terminate_calls == []
        assert harness.events_of(Event.SERVER_STARTED)[-1]["pid"] == tracked.pid

    def test_adopts_an_existing_process_as_running(self, harness):
        identity = harness.start_with_process()

        assert harness.controller.state == LifecycleState.RUNNING
        assert harness.pm.tracked_identity == identity
        assert harness.controller.get_snapshot()["state"] == "RUNNING"
        assert harness.events_of(Event.SERVER_STARTED)[0]["pid"] == identity.pid

    def test_start_command_commits_starting_then_running(self, harness):
        harness.pm.launch_identity = ProcessIdentity(2001, 20.0)
        harness.start()

        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

        states = [data["state"] for data in harness.events_of(Event.LIFECYCLE_CHANGED)]
        assert states[0] == LifecycleState.STARTING
        assert harness.events_of(Event.SERVER_STARTED)[0]["state"] == (
            LifecycleState.RUNNING
        )
        assert harness.pm.tracked_identity == ProcessIdentity(2001, 20.0)

    def test_tracking_is_recorded_before_the_started_notification(self, harness):
        identity = ProcessIdentity(2001, 20.0)
        harness.pm.launch_identity = identity
        tracked_at_notification = []
        harness.bus.subscribe(
            Event.SERVER_STARTED,
            lambda data: tracked_at_notification.append(harness.pm.tracked_identity),
        )
        harness.start()

        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

        assert tracked_at_notification == [identity]

    def test_failed_launch_returns_to_stopped_without_started_notification(
        self, harness
    ):
        harness.pm.launch_identity = None
        harness.start()

        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)
        assert harness.events_of(Event.SERVER_STARTED) == []

    def test_stop_commits_down_and_releases_players_before_notification(self, harness):
        harness.client.players = [["P1", "uid1", "111", "10"]]
        identity = harness.start_with_process()
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")

        online_at_stop_notification = []
        harness.bus.subscribe(
            Event.SERVER_STOPPED,
            lambda data: online_at_stop_notification.append(
                harness.players.get_player_count()
            ),
        )

        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

        snapshot = harness.controller.get_snapshot()
        assert snapshot["state"] == LifecycleState.STOPPED
        assert snapshot["playerData"] == "unknown"
        assert snapshot["playerCount"] == 0
        assert [player["steam_id"] for player in snapshot["players"]] == ["111"]
        assert snapshot["players"][0]["currently_online"] is False
        assert online_at_stop_notification == [0]
        assert harness.pm.tracked_identity is None
        assert harness.events_of(Event.SERVER_STOPPED)[0]["pid"] == identity.pid
        states = [data["state"] for data in harness.events_of(Event.LIFECYCLE_CHANGED)]
        assert states[-1] == LifecycleState.STOPPING

    def test_blocked_rest_response_after_stop_is_discarded(self, harness):
        harness.client.players = [["Ghost", "uid9", "999", "50"]]
        harness.client.block = threading.Event()
        harness.client.entered.clear()
        identity = harness.start_with_process()
        wait_until(lambda: harness.client.entered.is_set())

        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

        harness.client.block.set()
        time.sleep(0.1)

        snapshot = harness.controller.get_snapshot()
        assert snapshot["state"] == LifecycleState.STOPPED
        assert snapshot["playerCount"] == 0
        assert snapshot["players"] == []
        assert harness.players.get_player_count() == 0
        assert harness.pm.identity is None
        assert harness.events_of(Event.SERVER_STARTED)[0]["pid"] == identity.pid

    def test_stop_and_restart_discards_the_previous_generations_roster(self, harness):
        harness.client.players = [["P1", "uid1", "111", "10"]]
        harness.start_with_process(ProcessIdentity(1001, 10.0))
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")

        harness.client.block = threading.Event()
        harness.client.entered.clear()
        wait_until(lambda: harness.client.entered.is_set())

        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

        harness.pm.launch_identity = ProcessIdentity(2002, 20.0)
        harness.client.players = []
        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

        harness.client.block.set()
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")
        time.sleep(0.1)

        snapshot = harness.controller.get_snapshot()
        assert snapshot["state"] == LifecycleState.RUNNING
        assert snapshot["playerCount"] == 0
        assert all(not player["currently_online"] for player in snapshot["players"])
        assert harness.players.get_player_count() == 0

    def test_rest_failure_keeps_roster_and_cancels_auto_stop(self, harness):
        harness.clock.now = 1000.0
        harness.controller._supervisor.last_server_started_time = 0.0
        harness.controller._supervisor.server_starting_cooldown = 0
        harness.controller._supervisor.server_stopping_cooldown = 0
        harness.client.players = [["P1", "uid1", "111", "10"]]
        harness.start_with_process()
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")

        # Enable auto-stop after the server is up and the roster is known.
        from src.lifecycle.supervisor import settings as controller_settings

        controller_settings.autoStop = True

        player_left_events = []
        harness.bus.subscribe(
            Event.PLAYER_LEFT, lambda data: player_left_events.append(data)
        )

        harness.client.available = False
        wait_until(
            lambda: harness.controller.get_snapshot()["playerData"] == "unavailable"
        )
        snapshot = harness.controller.get_snapshot()
        assert snapshot["state"] == LifecycleState.RUNNING
        assert snapshot["playerCount"] == 1
        assert snapshot["players"][0]["currently_online"] is True
        assert player_left_events == []

        harness.clock.advance(120)
        time.sleep(0.1)
        assert harness.controller.state == LifecycleState.RUNNING

        harness.client.available = True
        harness.client.players = []
        wait_until(
            lambda: (
                harness.controller.get_snapshot()["playerData"] == "ok"
                and harness.controller.get_snapshot()["playerCount"] == 0
            )
        )

        harness.clock.advance(settings_auto_stop_delay(harness) + 5)
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

    def test_old_auto_stop_deadline_cannot_stop_a_restarted_generation(self, harness):
        from src.lifecycle.supervisor import settings as controller_settings

        controller_settings.autoStop = True
        harness.client.players = []
        harness.start_with_process(ProcessIdentity(1001, 10.0))
        wait_until(lambda: harness.controller.get_snapshot()["playerData"] == "ok")

        harness.clock.advance(30)
        time.sleep(0.05)

        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

        harness.pm.launch_identity = ProcessIdentity(2002, 20.0)
        assert harness.controller.start_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

        # The first generation's deadline was 60 s after it started; the
        # restart happened halfway through, so crossing it must not stop gen 2.
        harness.clock.advance(31)
        time.sleep(0.1)
        assert harness.controller.state == LifecycleState.RUNNING

        harness.clock.advance(settings_auto_stop_delay(harness) + 5)
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

    def test_restart_waits_for_old_stop_and_discards_its_stale_result(self, harness):
        first = harness.start_with_process(ProcessIdentity(1001, 10.0))

        harness.pm.terminate_block = threading.Event()
        harness.pm.terminate_mutates = False
        assert harness.controller.stop_server() is True
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPING)
        assert harness.pm.terminate_entered.wait(1)

        # The old process exits on its own and the supervisor commits DOWN.
        harness.pm.alive.discard(first.pid)
        harness.pm.found = None
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)

        harness.pm.launch_identity = ProcessIdentity(2002, 20.0)
        assert harness.controller.start_server() is True
        assert harness.controller.state == LifecycleState.STARTING
        assert harness.pm.launch_calls == []

        # The shared worker finishes the old stop before launching a replacement.
        harness.pm.terminate_block.set()
        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)

        assert harness.controller.state == LifecycleState.RUNNING
        assert len(harness.events_of(Event.SERVER_STOPPED)) == 1

    def test_duplicate_process_observations_commit_down_once(self, harness):
        identity = harness.start_with_process()

        harness.pm.alive.discard(identity.pid)
        harness.pm.found = None
        wait_until(lambda: harness.controller.state == LifecycleState.STOPPED)
        time.sleep(0.1)

        assert len(harness.events_of(Event.SERVER_STOPPED)) == 1
        assert harness.controller.state == LifecycleState.STOPPED

    def test_external_replacement_is_adopted_as_a_new_generation(self, harness):
        first = harness.start_with_process(ProcessIdentity(1001, 10.0))

        replacement = ProcessIdentity(2002, 20.0)
        harness.pm.alive.discard(first.pid)
        harness.pm.alive.add(replacement.pid)
        harness.pm.found = replacement

        wait_until(lambda: harness.controller.state == LifecycleState.RUNNING)
        wait_until(lambda: harness.pm.tracked_identity == replacement)
        assert harness.controller.generation != 1
        assert harness.events_of(Event.SERVER_STOPPED)[0]["pid"] == first.pid
        assert harness.events_of(Event.SERVER_STARTED)[-1]["pid"] == replacement.pid

    def test_repeated_start_and_close_leave_no_threads_or_subscriptions(self, harness):
        baseline = harness.bus.subscriber_count()
        for _ in range(3):
            harness.controller.start()
            harness.controller.close()

        assert harness.bus.subscriber_count() == baseline
        assert not any(
            thread.name in CONTROLLER_THREAD_NAMES for thread in threading.enumerate()
        )
        assert harness.pm.closed is True


@pytest.mark.parametrize(
    "action,storage", [("ban_player", "add_ban"), ("unban_player", "remove_ban")]
)
@pytest.mark.parametrize("api_success,storage_success", [(True, False), (False, True)])
def test_moderation_reports_api_and_storage_failures(
    action, storage, api_success, storage_success
):
    controller = PalWorldController.__new__(PalWorldController)
    controller.client = MagicMock()
    controller.banlist_manager = MagicMock()
    getattr(controller.client, action).return_value = api_success
    getattr(controller.banlist_manager, storage).return_value = storage_success

    assert getattr(controller, action)("steam_123") is False
    assert getattr(controller.banlist_manager, storage).call_count == int(api_success)


def settings_auto_stop_delay(harness):
    from src.lifecycle.supervisor import settings as controller_settings

    return controller_settings.autoStopDelay


def _dummy_process_manager(pid_file):
    from src.process_manager import OSProcessManager

    class DummyManager(OSProcessManager):
        def pid_file_name(self):
            return str(pid_file)

    return DummyManager()


def test_no_such_process_during_terminate_converges_to_down(tmp_path):
    """A stop whose process is already gone is still a confirmed DOWN."""
    from unittest.mock import patch

    import psutil

    manager = _dummy_process_manager(tmp_path / "dummy.pid")
    identity = ProcessIdentity(4242, 10.0)
    manager.set_known_identity(identity)

    with patch(
        "src.process_manager.psutil.Process",
        side_effect=psutil.NoSuchProcess(4242),
    ):
        assert manager.terminate_process(identity) is True

    assert manager.tracked_identity is None


def test_expected_identity_compare_and_clear_never_clears_a_replacement(tmp_path):
    manager = _dummy_process_manager(tmp_path / "dummy.pid")
    original = ProcessIdentity(111, 1.0)
    replacement = ProcessIdentity(222, 2.0)

    manager.set_known_identity(original)
    manager.set_known_identity(replacement)

    assert manager.clear_identity(original) is False
    assert manager.tracked_identity == replacement

    # Same PID with a different creation time is a different process too.
    assert manager.clear_identity(ProcessIdentity(222, 3.0)) is False
    assert manager.tracked_identity == replacement

    assert manager.clear_identity(replacement) is True
    assert manager.tracked_identity is None
