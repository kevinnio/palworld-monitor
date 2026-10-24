"""Runtime tests: blocking I/O, stale responses and worker cleanup."""

import threading
import time

import pytest

from src.banlist_manager import BanlistManager
from src.events import EventBus
from src.lifecycle.messages import LifecycleState, ProcessIdentity, StopCommand
from src.lifecycle.runtime import LifecycleRuntime
from src.lifecycle.supervisor import Supervisor
from src.player_manager import PlayerManager
from test.test_lifecycle_supervisor import FakeClock, FakeProcessManager

CONTROLLER_THREAD_NAMES = {
    "palworld-lifecycle",
    "palworld-detection",
    "palworld-poll",
    "palworld-launch",
    "palworld-stop",
}


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


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.fixture
def runtime_env(mock_settings, tmp_path):
    mock_settings.pollingRate = 0.01
    mock_settings.autoStop = False
    bus = EventBus()
    pm = FakeProcessManager()
    client = FakeClient()
    players = PlayerManager(bus=bus, data_file=str(tmp_path / "players.json"))
    bans = BanlistManager(bus=bus, banlist_path=str(tmp_path / "banlist.txt"))
    clock = FakeClock()
    supervisor = Supervisor(pm, players, bans, bus, clock=clock)
    supervisor.poll_interval = 0
    runtime = LifecycleRuntime(supervisor, client, pm)
    yield runtime, supervisor, client, pm, bus
    runtime.close()


class TestLifecycleRuntime:
    def test_blocked_rest_response_after_stop_is_discarded(self, runtime_env):
        runtime, supervisor, client, pm, _bus = runtime_env
        identity = ProcessIdentity(1001, 10.0)
        pm.found = identity
        pm.alive.add(identity.pid)
        runtime.start()
        wait_until(lambda: supervisor.state == LifecycleState.RUNNING)
        wait_until(lambda: supervisor.get_snapshot()["playerData"] == "ok")

        client.block = threading.Event()
        client.entered.clear()
        wait_until(lambda: client.entered.is_set())

        assert runtime.submit_command(StopCommand()) is True
        wait_until(lambda: supervisor.state == LifecycleState.STOPPED)

        client.players = [["Ghost", "uid9", "999", "50"]]
        client.block.set()
        time.sleep(0.1)

        snapshot = supervisor.get_snapshot()
        assert snapshot["state"] == LifecycleState.STOPPED
        assert snapshot["playerCount"] == 0
        assert snapshot["players"] == []

    def test_repeated_start_close_leaves_no_threads_or_subscriptions(
        self, runtime_env
    ):
        runtime, _supervisor, _client, _pm, bus = runtime_env
        baseline = bus.subscriber_count()

        for _ in range(3):
            runtime.start()
            runtime.close()

        assert bus.subscriber_count() == baseline
        assert not any(
            thread.name in CONTROLLER_THREAD_NAMES for thread in threading.enumerate()
        )

    def test_command_before_start_is_rejected(self, runtime_env):
        runtime, _supervisor, _client, _pm, _bus = runtime_env
        assert runtime.submit_command(StopCommand()) is False
