"""Startup behavior for the feature flags.

`main()` is the only place that reads `features.autoStart` and `web.enabled`, so
these tests run the real auto-start manager and record whether the app actually
binds the game port.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.main as main_module
from src.auto_start import AutoStartManager
from src.events import EventBus
from src.lifecycle.messages import LifecycleState


class RecordingAutoStart(AutoStartManager):
    """The real manager, recording how often it manages to bind the game port."""

    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.port_opens = 0
        type(self).instances.append(self)

    def open_palworld_port_socket(self):
        opened = super().open_palworld_port_socket()
        self.port_opens += int(bool(opened))
        return opened


class FakeWebServer:
    """Stands in for the dashboard; returns once the listener had its chance."""

    def __init__(self, *args, **kwargs):
        self.runs = 0
        self.closed = False

    def run(self):
        self.runs += 1
        deadline = time.time() + 1
        while time.time() < deadline and not any(
            manager.sock is not None for manager in RecordingAutoStart.instances
        ):
            time.sleep(0.01)

    def close(self):
        self.closed = True


@pytest.fixture
def startup(mock_settings, tmp_path, monkeypatch):
    """`main()` with its I/O faked; returns the pieces it should wire."""
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("", encoding="utf-8")
    mock_settings.palworldServerPort = 0  # ephemeral: never fight a real server
    mock_settings.webServerPort = 8213

    fakes = SimpleNamespace(
        manager=MagicMock(),
        controller=MagicMock(),
        web_server=FakeWebServer(),
        web_server_cls=MagicMock(),
    )
    fakes.manager.find_process_identity.return_value = None
    fakes.controller.state = LifecycleState.STOPPED
    fakes.controller.start_server.return_value = False
    fakes.web_server_cls.return_value = fakes.web_server
    fakes.bus = EventBus()

    monkeypatch.setattr(RecordingAutoStart, "instances", [])
    monkeypatch.setattr(
        main_module,
        "parse_args",
        lambda: SimpleNamespace(appDir=None, logDir=None, settings=None),
    )
    monkeypatch.setattr(main_module, "settings", mock_settings)
    monkeypatch.setattr(main_module, "EventBus", lambda: fakes.bus)
    monkeypatch.setattr(main_module, "apply_overrides", lambda args: None)
    monkeypatch.setattr(main_module, "enter_app_dir", lambda app_dir: None)
    monkeypatch.setattr(main_module, "configure_logging", lambda config: config)
    monkeypatch.setattr(
        main_module,
        "_determine_settings_path",
        lambda explicit, app_dir: (str(settings_path), None),
    )
    monkeypatch.setattr(main_module, "create_process_manager", lambda: fakes.manager)
    monkeypatch.setattr(
        main_module, "PalWorldController", lambda **kwargs: fakes.controller
    )
    monkeypatch.setattr(main_module, "PlayerManager", MagicMock)
    monkeypatch.setattr(main_module, "BanlistManager", MagicMock)
    monkeypatch.setattr(main_module, "WebServer", fakes.web_server_cls)
    monkeypatch.setattr(main_module, "AutoStartManager", RecordingAutoStart)
    monkeypatch.setattr(main_module, "print_web_link", lambda port: None)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)  # banner
    monkeypatch.setattr("src.api_clients.RestClient", MagicMock)

    return fakes


class TestAutoStartFlag:
    def test_disabled_never_listens_on_the_game_port(self, startup, mock_settings):
        """features.autoStart: false — no listener, so a connect attempt starts nothing."""
        mock_settings.autoStart = False

        main_module.main()

        assert RecordingAutoStart.instances == []
        startup.manager.find_process_identity.assert_not_called()

    def test_enabled_listens_when_no_server_process_exists(
        self, startup, mock_settings
    ):
        mock_settings.autoStart = True

        main_module.main()

        assert len(RecordingAutoStart.instances) == 1
        manager = RecordingAutoStart.instances[0]
        assert manager.port_opens == 1
        # main() releases the port again before it returns.
        assert manager.sock is None
        assert manager.listen_thread is None

    def test_enabled_does_not_bind_over_an_undiscovered_server(
        self, startup, mock_settings
    ):
        """A server main() cannot see yet owns the port; do not fight it."""
        mock_settings.autoStart = True
        startup.manager.find_process_identity.return_value = MagicMock()

        main_module.main()

        assert len(RecordingAutoStart.instances) == 1
        assert RecordingAutoStart.instances[0].port_opens == 0


class TestWebFlag:
    def test_disabled_starts_no_web_server_and_keeps_monitoring(
        self, startup, mock_settings, monkeypatch
    ):
        """web.enabled: false — no dashboard, but the process must not exit."""
        mock_settings.useWebServer = False
        sleeps = []

        def stop_after_a_few_ticks(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:
                raise KeyboardInterrupt

        monkeypatch.setattr(main_module.time, "sleep", stop_after_a_few_ticks)

        main_module.main()

        startup.web_server_cls.assert_not_called()
        assert len(sleeps) == 3
        assert RecordingAutoStart.instances[0].listen_thread is None
        startup.controller.close.assert_called_once()

    def test_enabled_serves_the_dashboard(self, startup, mock_settings):
        mock_settings.useWebServer = True

        main_module.main()

        startup.web_server_cls.assert_called_once()
        assert startup.web_server.runs == 1
        assert startup.web_server.closed is True
