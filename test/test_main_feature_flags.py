"""Startup wiring when a feature flag is disabled.

`main()` is the only place that reads `features.autoStart` and `web.enabled`, so
these tests pin what the app does *not* build when those flags are off.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.main as main_module


@pytest.fixture
def startup(mock_settings, tmp_path, monkeypatch):
    """`main()` with its side effects faked; returns the pieces it should wire."""
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("", encoding="utf-8")
    mock_settings.webServerPort = 8213

    fakes = SimpleNamespace(
        manager=MagicMock(),
        controller=MagicMock(),
        web_server=MagicMock(),
        web_server_cls=MagicMock(),
        auto_start=MagicMock(),
        auto_start_cls=MagicMock(),
    )
    fakes.manager.find_process_identity.return_value = None
    fakes.web_server_cls.return_value = fakes.web_server
    fakes.auto_start_cls.return_value = fakes.auto_start

    monkeypatch.setattr(main_module, "parse_args", lambda: SimpleNamespace(
        appDir=None, logDir=None, settings=None
    ))
    monkeypatch.setattr(main_module, "settings", mock_settings)
    monkeypatch.setattr(main_module, "apply_overrides", lambda args: None)
    monkeypatch.setattr(main_module, "enter_app_dir", lambda app_dir: None)
    monkeypatch.setattr(main_module, "configure_logging", lambda config: config)
    monkeypatch.setattr(
        main_module, "_determine_settings_path", lambda explicit, app_dir: (str(settings_path), None)
    )
    monkeypatch.setattr(main_module, "create_process_manager", lambda: fakes.manager)
    monkeypatch.setattr(main_module, "PalWorldController", lambda **kwargs: fakes.controller)
    monkeypatch.setattr(main_module, "PlayerManager", MagicMock)
    monkeypatch.setattr(main_module, "BanlistManager", MagicMock)
    monkeypatch.setattr(main_module, "WebServer", fakes.web_server_cls)
    monkeypatch.setattr(main_module, "AutoStartManager", fakes.auto_start_cls)
    monkeypatch.setattr(main_module, "print_web_link", lambda port: None)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)  # banner
    monkeypatch.setattr("src.api_clients.RestClient", MagicMock)

    return fakes


class TestAutoStartFlag:
    def test_disabled_never_listens_on_the_game_port(self, startup, mock_settings):
        """features.autoStart: false — no listener, so a connect attempt starts nothing."""
        mock_settings.autoStart = False

        main_module.main()

        startup.auto_start_cls.assert_not_called()
        startup.manager.find_process_identity.assert_not_called()

    def test_enabled_listens_when_no_server_process_exists(self, startup, mock_settings):
        mock_settings.autoStart = True

        main_module.main()

        startup.auto_start_cls.assert_called_once()
        startup.auto_start.listen_palworld_access.assert_called_once()

    def test_enabled_does_not_bind_over_an_undiscovered_server(self, startup, mock_settings):
        """A server main() cannot see yet owns the port; do not fight it."""
        mock_settings.autoStart = True
        startup.manager.find_process_identity.return_value = MagicMock()

        main_module.main()

        startup.auto_start.listen_palworld_access.assert_not_called()


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
        startup.auto_start.close.assert_called_once()
        startup.controller.close.assert_called_once()

    def test_enabled_serves_the_dashboard(self, startup, mock_settings):
        mock_settings.useWebServer = True

        main_module.main()

        startup.web_server_cls.assert_called_once()
        startup.web_server.run.assert_called_once()
        startup.web_server.close.assert_called_once()
