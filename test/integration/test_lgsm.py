"""Real Linux script/process coverage, without downloading LinuxGSM or Palworld."""

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from src.api_clients import RestClient
from src.banlist_manager import BanlistManager
from src.events import EventBus
from src.lifecycle.messages import LifecycleState, StartCommand, StopCommand
from src.lifecycle.runtime import LifecycleRuntime
from src.lifecycle.supervisor import Supervisor
from src.player_manager import PlayerManager
from src.process_manager import LGSMProcessManager, create_process_manager
from test.support import wait_until

pytestmark = [
    pytest.mark.linux,
    pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux executables"),
]


@pytest.fixture
def lgsm(tmp_path, monkeypatch, mock_settings, test_sleep_script):
    # Never adopt or stop a server belonging to somebody running tests locally.
    if LGSMProcessManager().find_process_identity("PalServer") is not None:
        pytest.skip("An existing PalServer process is running")

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", 8212))
        except OSError:
            pytest.skip("Dummy server port 8212 is already in use")

    install = tmp_path / "lgsm installation"
    install.mkdir()
    source = Path(__file__).parents[1] / "fixtures" / "lgsm" / "pwserver.py"
    script = install / "pwserver"
    script.write_text(f"#!{sys.executable}\n" + source.read_text())
    script.chmod(0o700)
    (install / "dummy-path").write_text(str(Path(test_sleep_script).resolve()))

    # Keep monitor PID/data files separate from the script's working directory.
    monkeypatch.chdir(tmp_path)
    mock_settings.useLGSM = True
    mock_settings.palworldServerExePath = str(script)
    mock_settings.palworldExeArguments = ""
    mock_settings.palworldServerAdminPassword = "palworld123"
    mock_settings.pollingRate = 0.05
    mock_settings.autoStop = False
    manager = create_process_manager()
    assert isinstance(manager, LGSMProcessManager)
    manager.STARTUP_TIMEOUT = 5
    manager.STARTUP_POLL_INTERVAL = 0.01
    manager.LGSM_COMMAND_TIMEOUT = 2

    with (install / "server.log").open("w") as log:
        service = subprocess.Popen(
            [str(script), "serve"], cwd=install, stdout=log, stderr=log
        )
        try:
            yield install, manager
        finally:
            service.terminate()
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)


@pytest.fixture
def make_runtime(lgsm, tmp_path):
    runtimes = []

    def make(manager):
        bus = EventBus()
        players = PlayerManager(bus=bus, data_file=str(tmp_path / "players.json"))
        bans = BanlistManager(bus=bus, banlist_path=str(tmp_path / "banlist.txt"))
        supervisor = Supervisor(manager, players, bans, bus)
        supervisor.server_starting_cooldown = 0
        supervisor.server_stopping_cooldown = 0
        runtime = LifecycleRuntime(supervisor, RestClient(), manager)
        runtimes.append(runtime)
        runtime.start()
        return runtime, supervisor

    yield make

    for runtime in runtimes:
        runtime.close()


def test_start_delayed_stop_and_pid_cleanup(lgsm, make_runtime):
    install, manager = lgsm
    runtime, supervisor = make_runtime(manager)

    assert runtime.submit_command(StartCommand())
    wait_until(lambda: supervisor.state == LifecycleState.RUNNING)
    wait_until(lambda: supervisor.get_snapshot()["playerData"] == "ok")
    identity = manager.tracked_identity
    assert identity is not None
    assert identity.create_time is not None
    assert identity.pid == int((install / "server.pid").read_text())
    assert Path(manager.pid_file_name()).read_text() == str(identity.pid)
    assert supervisor.get_snapshot()["playerCount"] == 3

    assert runtime.submit_command(StopCommand())
    wait_until(lambda: (install / "stop-request").exists())
    assert supervisor.state == LifecycleState.STOPPING
    assert manager.identity_is_running(identity)
    assert manager.tracked_identity == identity

    (install / "release-stop").touch()
    wait_until(lambda: supervisor.state == LifecycleState.STOPPED)
    wait_until(lambda: not Path(manager.pid_file_name()).exists())
    assert not manager.identity_is_running(identity)
    assert manager.tracked_identity is None
    assert (install / "commands").read_text().splitlines() == ["start", "stop"]


def test_restart_adopts_process_and_stops_via_settings(lgsm, make_runtime):
    install, manager = lgsm
    runtime, supervisor = make_runtime(manager)
    assert runtime.submit_command(StartCommand())
    wait_until(lambda: supervisor.state == LifecycleState.RUNNING)
    identity = manager.tracked_identity
    runtime.close()
    assert manager.identity_is_running(identity)

    reloaded = create_process_manager()
    assert reloaded.tracked_identity == identity
    runtime, supervisor = make_runtime(reloaded)
    wait_until(lambda: supervisor.state == LifecycleState.RUNNING)
    assert reloaded.tracked_identity == identity
    assert (install / "commands").read_text().splitlines() == ["start"]

    (install / "release-stop").touch()
    assert runtime.submit_command(StopCommand())
    wait_until(lambda: supervisor.state == LifecycleState.STOPPED)
    wait_until(lambda: not Path(reloaded.pid_file_name()).exists())
    assert not reloaded.identity_is_running(identity)
    assert reloaded.tracked_identity is None
    assert (install / "commands").read_text().splitlines() == ["start", "stop"]


@pytest.mark.parametrize("mode", ["fail-start", "no-process"])
def test_start_without_server_does_not_record_pid(lgsm, mode):
    install, manager = lgsm
    (install / mode).touch()
    manager.STARTUP_TIMEOUT = 0.2

    assert manager.launch_process(str(install / "pwserver"), "") is None
    assert manager.tracked_identity is None
    assert not Path(manager.pid_file_name()).exists()
    assert (install / "commands").read_text().splitlines() == ["start"]


@pytest.mark.parametrize("mode", ["fail-stop", "timeout-stop"])
def test_failed_stop_keeps_tracking_until_observed_exit(
    lgsm, make_runtime, mode, caplog
):
    install, manager = lgsm
    runtime, supervisor = make_runtime(manager)
    assert runtime.submit_command(StartCommand())
    wait_until(lambda: supervisor.state == LifecycleState.RUNNING)
    identity = manager.tracked_identity
    (install / mode).touch()
    manager.LGSM_COMMAND_TIMEOUT = 0.2

    assert runtime.submit_command(StopCommand())
    wait_until(lambda: (install / "stop-request").exists())
    assert supervisor.state == LifecycleState.STOPPING
    assert manager.tracked_identity == identity
    assert manager.identity_is_running(identity)

    # Let the real stop invocation finish or time out before releasing shutdown.
    wait_until(
        lambda: "waiting for process detection to confirm the exit" in caplog.text
    )
    assert manager.tracked_identity == identity
    (install / "release-stop").touch()
    wait_until(lambda: supervisor.state == LifecycleState.STOPPED)
    wait_until(lambda: not Path(manager.pid_file_name()).exists())
    assert not manager.identity_is_running(identity)
    assert manager.tracked_identity is None
