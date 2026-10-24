"""Direct tests for synchronous supervisor message handling."""

from types import SimpleNamespace

import pytest

from src.banlist_manager import BanlistManager
from src.events import Event, EventBus
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
from src.lifecycle.supervisor import Supervisor
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
        self.launch_calls = []
        self.terminate_calls = []

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
        return ProcessSnapshot(
            tracked=self.identity,
            tracked_running=self.identity_is_running(self.identity),
            found=self.found,
        )

    def launch_process(self, exe_path, exe_args):
        self.launch_calls.append((exe_path, exe_args))
        self.identity = self.launch_identity
        if self.identity is not None:
            self.alive.add(self.identity.pid)
            self.found = self.identity
        return self.identity

    def terminate_process(self, expected):
        self.terminate_calls.append(expected)
        if self.identity is not None and self.identity.matches(expected):
            self.alive.discard(self.identity.pid)
            self.clear_identity(self.identity)
            self.found = None
        return True


@pytest.fixture
def env(mock_settings, tmp_path):
    mock_settings.pollingRate = 0.01
    mock_settings.autoStop = False
    mock_settings.autoStopDelay = 60
    bus = EventBus()
    pm = FakeProcessManager()
    players = PlayerManager(bus=bus, data_file=str(tmp_path / "players.json"))
    bans = BanlistManager(bus=bus, banlist_path=str(tmp_path / "banlist.txt"))
    clock = FakeClock()
    supervisor = Supervisor(pm, players, bans, bus, clock=clock)
    supervisor.poll_interval = 0
    supervisor.server_starting_cooldown = 0
    supervisor.server_stopping_cooldown = 0
    events = []
    bus.subscribe(
        Event.SERVER_STARTED, lambda d: events.append((Event.SERVER_STARTED, d))
    )
    bus.subscribe(
        Event.SERVER_STOPPED, lambda d: events.append((Event.SERVER_STOPPED, d))
    )
    bus.subscribe(
        Event.SERVER_STATUS, lambda d: events.append((Event.SERVER_STATUS, d))
    )
    bus.subscribe(
        Event.LIFECYCLE_CHANGED,
        lambda d: events.append((Event.LIFECYCLE_CHANGED, d)),
    )
    return SimpleNamespace(
        supervisor=supervisor,
        pm=pm,
        players=players,
        bus=bus,
        clock=clock,
        events=events,
    )


def start_running(env, identity=None):
    identity = identity or ProcessIdentity(1001, 10.0)
    env.pm.identity = identity
    env.pm.alive.add(identity.pid)
    env.pm.found = identity
    env.supervisor.handle(ProcessObservation(0, env.pm.observe()))
    assert env.supervisor.state == LifecycleState.RUNNING
    return identity


def events_of(env, event_type):
    return [data for kind, data in env.events if kind == event_type]


class TestSupervisor:
    def test_start_command_returns_launch_request(self, env):
        command = StartCommand()
        requests = env.supervisor.handle(command)

        assert command.result is True
        assert len(requests) == 1
        assert isinstance(requests[0], LaunchRequest)
        assert env.supervisor.state == LifecycleState.STARTING
        assert events_of(env, Event.LIFECYCLE_CHANGED)[0]["state"] == (
            LifecycleState.STARTING
        )

        identity = ProcessIdentity(2001, 20.0)
        env.pm.set_known_identity(identity)
        env.supervisor.handle(LaunchResult(requests[0].generation, identity))

        assert env.supervisor.state == LifecycleState.RUNNING
        assert events_of(env, Event.SERVER_STARTED)[0]["pid"] == identity.pid
        ticks = env.supervisor.on_tick()
        assert len(ticks) == 1
        assert isinstance(ticks[0], PollRequest)

    def test_roster_commit_updates_snapshot(self, env):
        start_running(env)
        generation = env.supervisor.generation
        identity = env.pm.identity

        env.supervisor.handle(
            RosterSample(
                generation,
                identity,
                1,
                [["P1", "uid1", "111", "10"]],
            )
        )

        snapshot = env.supervisor.get_snapshot()
        assert snapshot["state"] == LifecycleState.RUNNING
        assert snapshot["playerData"] == "ok"
        assert snapshot["playerCount"] == 1
        assert snapshot["players"][0]["steam_id"] == "111"
        assert events_of(env, Event.SERVER_STATUS)[-1]["playerCount"] == 1

    def test_stale_process_observation_cannot_adopt_after_down(self, env):
        identity = start_running(env)
        old_generation = env.supervisor.generation
        stale_snapshot = ProcessSnapshot(
            tracked=identity, tracked_running=True, found=identity
        )

        stop = StopCommand()
        requests = env.supervisor.handle(stop)
        assert isinstance(requests[0], StopRequest)
        env.supervisor.handle(StopResult(requests[0].generation, identity, True))
        assert env.supervisor.state == LifecycleState.STOPPED
        started_before = len(events_of(env, Event.SERVER_STARTED))

        env.supervisor.handle(ProcessObservation(old_generation, stale_snapshot))

        assert env.supervisor.state == LifecycleState.STOPPED
        assert len(events_of(env, Event.SERVER_STARTED)) == started_before

    def test_reused_live_pid_is_not_adopted_without_a_matching_scan(self, env):
        reused = ProcessIdentity(999, 1.0)
        env.pm.identity = reused
        env.pm.alive.add(reused.pid)
        env.pm.found = None

        env.supervisor.handle(ProcessObservation(0, env.pm.observe()))

        assert env.pm.tracked_identity is None
        assert env.supervisor.state == LifecycleState.STOPPED
        assert events_of(env, Event.SERVER_STARTED) == []
        assert reused.pid in env.pm.alive

        actual = ProcessIdentity(2002, 20.0)
        env.pm.found = actual
        env.pm.alive.add(actual.pid)
        env.supervisor.handle(ProcessObservation(0, env.pm.observe()))

        assert env.pm.tracked_identity == actual
        assert env.supervisor.state == LifecycleState.RUNNING
        assert reused.pid in env.pm.alive
        assert env.pm.terminate_calls == []

    def test_failed_stop_returns_to_running_and_requests_poll(self, env):
        identity = start_running(env)
        stop = StopCommand()
        requests = env.supervisor.handle(stop)
        assert isinstance(requests[0], StopRequest)

        env.supervisor.handle(StopResult(requests[0].generation, identity, False))

        assert env.supervisor.state == LifecycleState.RUNNING
        assert events_of(env, Event.LIFECYCLE_CHANGED)[-1]["state"] == (
            LifecycleState.RUNNING
        )
        ticks = env.supervisor.on_tick()
        assert len(ticks) == 1
        assert isinstance(ticks[0], PollRequest)

    def test_pending_stop_waits_for_exit(self, env):
        identity = start_running(env)
        requests = env.supervisor.handle(StopCommand())
        assert isinstance(requests[0], StopRequest)

        env.supervisor.handle(StopResult(requests[0].generation, identity, None))
        assert env.supervisor.state == LifecycleState.STOPPING

        env.pm.alive.discard(identity.pid)
        env.pm.found = None
        env.supervisor.handle(
            ProcessObservation(env.supervisor.generation, env.pm.observe())
        )
        assert env.supervisor.state == LifecycleState.STOPPED

    def test_start_guard_blocks_an_unadopted_found_process(self, env):
        found = ProcessIdentity(2002, 20.0)
        env.pm.found = found
        env.pm.alive.add(found.pid)

        command = StartCommand()
        requests = env.supervisor.handle(command)

        assert command.result is False
        assert requests == []
        assert env.supervisor.state == LifecycleState.STOPPED

        env.supervisor.handle(
            ProcessObservation(
                0,
                ProcessSnapshot(tracked=None, tracked_running=False, found=found),
            )
        )
        assert env.pm.tracked_identity == found
        assert env.supervisor.state == LifecycleState.RUNNING
