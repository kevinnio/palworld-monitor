"""
Tests for the app folder: where the app keeps settings, logs, player data and
pid files, and how --app-dir / PALWORLD_MONITOR_APP_DIR move it.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.cli import apply_overrides, parse_args
from src.main import (
    APP_DIR_ENV,
    SETTINGS_ENV,
    _absolute,
    _determine_settings_path,
    enter_app_dir,
    resolve_app_dir,
    resolve_startup_paths,
)
from src.settings import Settings


class TestAppFolder:
    """The app folder is where its files live: settings, logs, player data, pid files."""

    def test_source_run_uses_the_project_root_not_src(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

        app_dir = os.path.normpath(resolve_app_dir())

        assert app_dir == project_root
        assert os.path.basename(app_dir) != "src"

    def test_frozen_run_uses_the_exe_folder(self, tmp_path, monkeypatch):
        dist = os.path.join(str(tmp_path), "dist")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", os.path.join(dist, "palworld-monitor.exe"))

        assert os.path.normpath(resolve_app_dir()) == os.path.normpath(dist)

    def test_cli_flag_and_environment_move_the_app_folder(self, tmp_path, monkeypatch):
        chosen = os.path.join(str(tmp_path), "chosen")
        assert os.path.normpath(resolve_app_dir(chosen)) == os.path.normpath(chosen)

        monkeypatch.setenv(APP_DIR_ENV, os.path.join(str(tmp_path), "from-env"))
        assert os.path.normpath(resolve_app_dir()) == os.path.normpath(
            os.path.join(str(tmp_path), "from-env")
        )

    def test_cli_flag_wins_over_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv(APP_DIR_ENV, os.path.join(str(tmp_path), "from-env"))
        chosen = os.path.join(str(tmp_path), "from-flag")

        assert os.path.normpath(resolve_app_dir(chosen)) == os.path.normpath(chosen)

    def test_entering_the_app_folder_creates_it_and_moves_the_process(
        self, tmp_path, monkeypatch
    ):
        target = os.path.join(str(tmp_path), "app")
        monkeypatch.chdir(tmp_path)

        enter_app_dir(target)

        assert os.path.isdir(target)
        assert os.path.normpath(os.getcwd()) == os.path.normpath(target)

    def test_a_writable_app_folder_leaves_no_probe_file(self, tmp_path, monkeypatch):
        target = os.path.join(str(tmp_path), "app")
        monkeypatch.chdir(tmp_path)

        enter_app_dir(target)

        assert os.listdir(target) == []

    def test_an_unusable_app_folder_fails_clearly(self, tmp_path):
        blocker = os.path.join(str(tmp_path), "not-a-folder")
        with open(blocker, "w") as handle:
            handle.write("I am a file")

        with pytest.raises(OSError):
            enter_app_dir(os.path.join(blocker, "app"))

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permissions are the testable ones")
    def test_a_read_only_app_folder_is_rejected(self, tmp_path):
        target = os.path.join(str(tmp_path), "read-only")
        os.makedirs(target)
        os.chmod(target, 0o500)
        try:
            with pytest.raises(OSError):
                enter_app_dir(target)
        finally:
            os.chmod(target, 0o700)

    def test_blank_and_padded_paths_are_normalised(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        assert _absolute("   ") is None
        assert os.path.normpath(_absolute("  logs  ")) == os.path.normpath(
            os.path.join(str(tmp_path), "logs")
        )

    def test_relative_flags_are_resolved_where_the_user_typed_them(
        self, tmp_path, monkeypatch
    ):
        shell = os.path.join(str(tmp_path), "shell")
        os.makedirs(shell)
        monkeypatch.chdir(shell)
        args = SimpleNamespace(appDir="app", logDir=None, settings="conf/prod.yaml")

        paths = resolve_startup_paths(args)

        assert os.path.normpath(paths.app_dir) == os.path.normpath(
            os.path.join(shell, "app")
        )
        assert os.path.normpath(paths.settings_path) == os.path.normpath(
            os.path.join(shell, "conf", "prod.yaml")
        )

    def test_app_folder_flag_is_parsed_and_not_applied_as_a_setting(self, tmp_path):
        chosen = os.path.join(str(tmp_path), "app")
        settings = Settings()

        apply_overrides(parse_args(["--app-dir", chosen]), settings)

        assert settings.get("appDir") is None

    def test_settings_are_found_in_the_app_folder(self, tmp_path, monkeypatch):
        isolate_app_location(tmp_path, monkeypatch)

        found, note = _determine_settings_path(None, str(tmp_path))

        assert os.path.normpath(found) == os.path.normpath(
            os.path.join(str(tmp_path), "settings.yaml")
        )
        assert note is None

    def test_explicit_settings_path_wins(self, tmp_path, monkeypatch):
        isolate_app_location(tmp_path, monkeypatch)
        explicit = os.path.join(str(tmp_path), "configs", "other.yaml")

        found, note = _determine_settings_path(explicit, str(tmp_path))

        assert os.path.normpath(found) == os.path.normpath(explicit)
        assert note is None

        monkeypatch.setenv(SETTINGS_ENV, explicit)
        found, _ = _determine_settings_path(
            resolve_startup_paths(
                SimpleNamespace(settings=None, appDir=None, logDir=None)
            ).settings_path,
            str(tmp_path),
        )
        assert os.path.normpath(found) == os.path.normpath(explicit)

    def test_settings_left_in_src_are_read_with_a_note_to_move_them(self, tmp_path, monkeypatch):
        src_dir = os.path.join(str(tmp_path), "src")
        os.makedirs(src_dir)
        legacy = os.path.join(src_dir, "settings.yaml")
        with open(legacy, "w") as handle:
            handle.write("palserver:\n  port: 8211\n")
        isolate_app_location(tmp_path, monkeypatch)
        app_dir = os.path.join(str(tmp_path), "app")

        found, note = _determine_settings_path(None, app_dir)

        assert os.path.normpath(found) == os.path.normpath(legacy)
        assert legacy in note
        assert os.path.join(app_dir, "settings.yaml") in note


def isolate_app_location(tmp_path, monkeypatch):
    """Point the app's own location at a temp folder, so tests don't touch the repo."""
    monkeypatch.setattr(
        "src.main.__file__", os.path.join(str(tmp_path), "src", "main.py")
    )
