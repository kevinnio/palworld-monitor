"""Runtime tests: blocking I/O, stale responses and worker cleanup."""

import threading
import time

import pytest

from src.banlist_manager import BanlistManager
from src.events import EventBus
from src.lifecycle.messages import (
    LifecycleState,
    ProcessIdentity,
    StartCommand,
    StopCommand,
)
from src.lifecycle.runtime import LifecycleRuntime
from src.lifecycle.supervisor import Supervisor
from src.player_manager import PlayerManager
from test.support import wait_until
from test.test_lifecycle_supervisor import FakeClock, FakeProcessManager

CONTROLLER_THREAD_NAMES = {
    "palworld-lifecycle",
    "palworld-detection",
    "palworld-poll",
    "palworld-process",
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
    def test_starts_only_four_threads(self, runtime_env):
        runtime, _supervisor, _client, _pm, _bus = runtime_env
        runtime.start()

        assert set(runtime._threads) == CONTROLLER_THREAD_NAMES
        assert all(thread.is_alive() for thread in runtime._threads.values())

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

    def test_repeated_start_close_leaves_no_threads_or_subscriptions(self, runtime_env):
        runtime, _supervisor, _client, _pm, bus = runtime_env
        baseline = bus.subscriber_count()

        for _ in range(3):
            runtime.start()
            runtime.close()

        assert bus.subscriber_count() == baseline
        assert not any(
            thread.name in CONTROLLER_THREAD_NAMES for thread in threading.enumerate()
        )

    def test_commands_work_after_restart(self, runtime_env):
        runtime, supervisor, _client, pm, _bus = runtime_env
        supervisor.server_starting_cooldown = 0
        supervisor.server_stopping_cooldown = 0

        for pid in range(1001, 1004):
            pm.launch_identity = ProcessIdentity(pid, 10.0)
            runtime.start()
            try:
                assert runtime.submit_command(StartCommand()) is True
                wait_until(lambda: supervisor.state == LifecycleState.RUNNING)
                assert runtime.submit_command(StopCommand()) is True
                wait_until(lambda: supervisor.state == LifecycleState.STOPPED)
            finally:
                runtime.close()

    def test_cannot_restart_while_a_worker_is_still_blocked(
        self, runtime_env, monkeypatch
    ):
        runtime, supervisor, client, pm, _bus = runtime_env
        monkeypatch.setattr("src.lifecycle.runtime._WORKER_JOIN_TIMEOUT", 0.01)
        pm.found = ProcessIdentity(1001, 10.0)
        pm.alive.add(pm.found.pid)
        client.block = threading.Event()
        runtime.start()
        assert client.entered.wait(1)

        try:
            runtime.close()
            with pytest.raises(RuntimeError, match="still shutting down"):
                runtime.start()
        finally:
            client.block.set()
            wait_until(lambda: not any(t.is_alive() for t in runtime._threads.values()))

        runtime.start()
        wait_until(lambda: supervisor.get_snapshot()["playerData"] == "ok")

    def test_process_worker_survives_launch_and_stop_errors(
        self, runtime_env, monkeypatch
    ):
        runtime, supervisor, _client, pm, _bus = runtime_env
        supervisor.server_starting_cooldown = 0
        supervisor.server_stopping_cooldown = 0
        pm.launch_identity = ProcessIdentity(1001, 10.0)

        def fail(*_args):
            raise OSError("process operation failed")

        runtime.start()
        with monkeypatch.context() as patch:
            patch.setattr(pm, "launch_process", fail)
            assert runtime.submit_command(StartCommand()) is True
            wait_until(lambda: supervisor.state == LifecycleState.STOPPED)

        assert runtime.submit_command(StartCommand()) is True
        wait_until(lambda: supervisor.state == LifecycleState.RUNNING)
        with monkeypatch.context() as patch:
            patch.setattr(pm, "terminate_process", fail)
            assert runtime.submit_command(StopCommand()) is True
            wait_until(lambda: supervisor.state == LifecycleState.RUNNING)

        assert runtime.submit_command(StopCommand()) is True
        wait_until(lambda: supervisor.state == LifecycleState.STOPPED)

    def test_detection_confirms_delayed_lgsm_shutdown(
        self, runtime_env, monkeypatch, tmp_path
    ):
        from src.process_manager import LGSMProcessManager

        runtime, supervisor, _client, _pm, _bus = runtime_env
        monkeypatch.setattr(
            LGSMProcessManager, "pid_file_name", lambda _: str(tmp_path / "server.pid")
        )
        manager = LGSMProcessManager()
        identity = ProcessIdentity(1001, 10.0)
        alive = {identity.pid}
        monkeypatch.setattr(manager, "_run_lgsm_command", lambda *_args: True)
        monkeypatch.setattr(
            manager, "identity_is_running", lambda i: i is not None and i.pid in alive
        )
        monkeypatch.setattr(
            manager, "find_process_identity", lambda _name: identity if alive else None
        )
        runtime.process_manager = supervisor.process_manager = manager
        runtime.start()
        wait_until(lambda: supervisor.state == LifecycleState.RUNNING)

        assert runtime.submit_command(StopCommand()) is True
        wait_until(lambda: runtime._process_requests.empty())
        assert supervisor.state == LifecycleState.STOPPING
        assert manager.tracked_identity == identity

        alive.clear()
        wait_until(lambda: supervisor.state == LifecycleState.STOPPED)
        assert manager.tracked_identity is None
        assert not (tmp_path / "server.pid").exists()

    def test_command_before_start_is_rejected(self, runtime_env):
        runtime, _supervisor, _client, _pm, _bus = runtime_env
        assert runtime.submit_command(StopCommand()) is False

    def test_close_waits_for_an_in_flight_process_command(
        self, runtime_env, monkeypatch
    ):
        runtime, supervisor, _client, pm, _bus = runtime_env
        supervisor.server_starting_cooldown = 0
        monkeypatch.setattr("src.lifecycle.runtime._WORKER_JOIN_TIMEOUT", 0.01)
        entered = threading.Event()
        release = threading.Event()

        def slow_launch(*_args):
            entered.set()
            release.wait(30)
            return ProcessIdentity(1001, 10.0)

        monkeypatch.setattr(pm, "launch_process", slow_launch)
        runtime.start()
        threading.Thread(
            target=runtime.submit_command, args=(StartCommand(),), daemon=True
        ).start()
        assert entered.wait(2)

        closer = threading.Thread(target=runtime.close, daemon=True)
        closer.start()
        try:
            time.sleep(0.2)
            # The launch is still running, so close must not have returned.
            assert closer.is_alive()
        finally:
            release.set()

        closer.join(5)
        assert not closer.is_alive()
        assert not runtime._threads["palworld-process"].is_alive()
