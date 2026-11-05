"""
Tests for the logging setup in src/main.py: append-only log files, size-based
rotation with gzipped backups, retention, path resolution, and the fallback to
console-only logging when the log folder can't be written.
"""

import gzip
import io
import logging
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.cli import apply_overrides, parse_args
from src.main import (
    _APP_HANDLER_FLAG,
    APP_DIR_ENV,
    LOG_FILE_NAME,
    SETTINGS_ENV,
    LogConfig,
    _absolute,
    _determine_settings_path,
    _log_config,
    _release_app_handlers,
    _resolve_log_dir,
    configure_logging,
    enter_app_dir,
    enter_app_folder_and_start_logging,
    resolve_app_dir,
    resolve_startup_paths,
)
from src.settings import (
    DEFAULT_LOG_SETTINGS,
    MAX_LOG_BACKUP_COUNT,
    Settings,
    parse_size,
)

LOG_PREFIX = "line-"


class Sandbox:
    """A sandboxed log folder plus the captured console output of the test."""

    def __init__(self, log_dir, console):
        self.log_dir = log_dir
        self.console = console

    def log_files(self):
        """Return {filename: text} for every log file written, gzip included."""
        files = {}
        for entry in sorted(os.listdir(self.log_dir)):
            path = os.path.join(self.log_dir, entry)
            if entry.endswith(".gz"):
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    files[entry] = handle.read()
            else:
                with open(path, encoding="utf-8") as handle:
                    files[entry] = handle.read()
        return files


def config_for(log_dir, **overrides):
    """A LogConfig with the shipped defaults, with *overrides* applied."""
    values = {
        "log_dir": log_dir,
        "level": "INFO",
        "max_bytes": parse_size(DEFAULT_LOG_SETTINGS["logMaxSize"]),
        "backup_count": DEFAULT_LOG_SETTINGS["logBackupCount"],
        "rotate": DEFAULT_LOG_SETTINGS["logRotate"],
        "compress": DEFAULT_LOG_SETTINGS["logCompress"],
        "retention_days": DEFAULT_LOG_SETTINGS["logRetentionDays"],
    }
    values.update(overrides)
    return LogConfig(**values)


def write_lines(count, size=400):
    """Log *count* padded INFO lines, so a small maxBytes rotates repeatedly."""
    for index in range(count):
        logging.info(f"{LOG_PREFIX}{index:04d} " + "x" * size)


def backup(index, compressed=True):
    """A rotated backup file name, as the handler names it."""
    return f"{LOG_FILE_NAME}.{index}{'.gz' if compressed else ''}"


@pytest.fixture
def logging_sandbox(tmp_path, monkeypatch):
    """Configure logging for a test, then restore the root logger.

    Console output is captured into a buffer instead of the test runner's
    stderr, so tests can assert on it.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    console = io.StringIO()
    monkeypatch.setattr(sys, "stderr", console)

    sandbox = Sandbox(log_dir=str(tmp_path / "logs"), console=console)
    try:
        yield sandbox
    finally:
        for handler in list(root.handlers):
            if getattr(handler, _APP_HANDLER_FLAG, False):
                root.removeHandler(handler)
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


class TestAppendsAndConsole:
    """The log file survives restarts, and the console keeps mirroring it."""

    def test_log_file_is_appended_to_across_setup_calls(self, logging_sandbox):
        configure_logging(config_for(logging_sandbox.log_dir))
        logging.info("first run line")

        # A restart of the app: same folder, same file, history must survive.
        configure_logging(config_for(logging_sandbox.log_dir))
        logging.info("second run line")

        text = logging_sandbox.log_files()[LOG_FILE_NAME]
        assert "first run line" in text
        assert "second run line" in text
        assert text.index("first run line") < text.index("second run line")

    def test_console_and_file_receive_the_same_records(self, logging_sandbox):
        configure_logging(config_for(logging_sandbox.log_dir))
        logging.warning("mirror me")

        assert "mirror me" in logging_sandbox.console.getvalue()
        assert "mirror me" in logging_sandbox.log_files()[LOG_FILE_NAME]

    def test_configure_logging_returns_the_applied_config(self, logging_sandbox):
        config = config_for(logging_sandbox.log_dir)

        returned = configure_logging(config)

        assert returned == config
        handlers = logging.getLogger().handlers
        app_file_handlers = [
            h
            for h in handlers
            if getattr(h, _APP_HANDLER_FLAG, False) and isinstance(h, logging.FileHandler)
        ]
        assert len(app_file_handlers) == 1
        assert app_file_handlers[0].baseFilename == os.path.join(
            logging_sandbox.log_dir, LOG_FILE_NAME
        )
        assert logging.getLogger().level == logging.INFO

    def test_a_released_handler_does_not_reopen_the_file(self, logging_sandbox):
        configure_logging(config_for(logging_sandbox.log_dir))
        root = logging.getLogger()
        app_handlers = [
            handler
            for handler in root.handlers
            if getattr(handler, _APP_HANDLER_FLAG, False)
        ]
        file_handler = next(
            handler for handler in app_handlers if isinstance(handler, logging.FileHandler)
        )
        console = next(
            handler for handler in app_handlers if not isinstance(handler, logging.FileHandler)
        )

        _release_app_handlers()

        record = logging.LogRecord(
            "stale", logging.INFO, __file__, 1, "after-close", None, None
        )
        file_handler.handle(record)
        console.handle(record)

        # A reference captured before the release must not write or reopen.
        assert file_handler.stream is None
        assert "after-close" not in logging_sandbox.log_files().get(LOG_FILE_NAME, "")
        assert "after-close" not in logging_sandbox.console.getvalue()

    def test_reconfiguring_leaves_foreign_handlers_in_place(self, logging_sandbox):
        foreign = logging.NullHandler()
        logging.getLogger().addHandler(foreign)
        try:
            configure_logging(config_for(logging_sandbox.log_dir))
            configure_logging(config_for(logging_sandbox.log_dir))

            handlers = logging.getLogger().handlers
            assert foreign in handlers
            app_handlers = [h for h in handlers if getattr(h, _APP_HANDLER_FLAG, False)]
            assert len(app_handlers) == 2  # one console, one file
        finally:
            logging.getLogger().removeHandler(foreign)


class TestRotationAndCompression:
    """Size-based rotation, gzipped backups, backup count and age caps."""

    def test_rotates_by_size_and_gzips_the_backup(self, logging_sandbox):
        configure_logging(config_for(logging_sandbox.log_dir, max_bytes=1000, backup_count=3))
        write_lines(4)

        files = logging_sandbox.log_files()
        rolled = backup(1)
        assert rolled in files
        assert f"{LOG_PREFIX}0000" in files[rolled]
        # The active file starts over after the rollover, and the backup is compressed.
        assert len(files[LOG_FILE_NAME]) < 1000
        assert len(files[rolled]) < 1000

    def test_leftover_history_rolls_on_the_first_record_after_a_restart(self, logging_sandbox):
        os.makedirs(logging_sandbox.log_dir, exist_ok=True)
        leftover = os.path.join(logging_sandbox.log_dir, LOG_FILE_NAME)
        with open(leftover, "w", encoding="utf-8") as handle:
            handle.write("y" * 1500)

        configure_logging(config_for(logging_sandbox.log_dir, max_bytes=1000, backup_count=2))
        logging.info("first line after a restart")

        files = logging_sandbox.log_files()
        # The size cap counts what earlier runs appended, so the old history is
        # moved to a compressed backup instead of being truncated.
        assert "y" * 100 in files[backup(1)]
        assert "first line after a restart" in files[LOG_FILE_NAME]
        assert len(files[LOG_FILE_NAME]) < 1000

    def test_history_under_half_the_cap_is_not_rotated(self, logging_sandbox):
        os.makedirs(logging_sandbox.log_dir, exist_ok=True)
        leftover = os.path.join(logging_sandbox.log_dir, LOG_FILE_NAME)
        with open(leftover, "w", encoding="utf-8") as handle:
            handle.write("y" * 3000)

        configure_logging(config_for(logging_sandbox.log_dir, max_bytes=5000, backup_count=2))
        logging.info("first line after a restart")

        files = logging_sandbox.log_files()
        # The size already on disk is counted once, not twice: 3000 bytes of
        # history plus one line still fit, so the file keeps growing.
        assert list(files) == [LOG_FILE_NAME]
        assert files[LOG_FILE_NAME].startswith("y" * 100)
        assert "first line after a restart" in files[LOG_FILE_NAME]

    def test_backup_count_zero_keeps_only_the_active_file(self, logging_sandbox):
        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=1000, backup_count=0)
        )
        write_lines(8)

        files = logging_sandbox.log_files()
        # No backups: the active file starts over at the cap instead.
        assert list(files) == [LOG_FILE_NAME]
        assert len(files[LOG_FILE_NAME]) < 1500

    def test_plain_backups_from_an_earlier_run_shift_and_age_out(self, logging_sandbox):
        os.makedirs(logging_sandbox.log_dir, exist_ok=True)
        plain = os.path.join(logging_sandbox.log_dir, backup(1, compressed=False))
        with open(plain, "w", encoding="utf-8") as handle:
            handle.write("plain history from a run with compression off")

        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=1000, backup_count=2, compress=True)
        )
        write_lines(4)

        files = logging_sandbox.log_files()
        # Compression toggled on: the old plain backup shifts to .2 and the new
        # one is compressed, so both forms are bounded by the same count cap.
        assert backup(1) in files
        assert backup(2, compressed=False) in files
        assert backup(1, compressed=False) not in files
        assert len([name for name in files if name != LOG_FILE_NAME]) <= 2

    def test_the_count_cap_covers_both_backup_forms_together(self, logging_sandbox):
        # Both forms at the same index used to survive the cap: .1.gz, .2, .2.gz
        # with backupCount 2. The cap counts files, not indices.
        configure_logging(
            config_for(
                logging_sandbox.log_dir, max_bytes=1000, backup_count=2, compress=False
            )
        )
        write_lines(4)
        configure_logging(
            config_for(
                logging_sandbox.log_dir, max_bytes=1000, backup_count=2, compress=True
            )
        )
        write_lines(4)

        files = logging_sandbox.log_files()
        backups = [name for name in files if name != LOG_FILE_NAME]
        assert len(backups) <= 2

    def test_expired_backups_are_pruned_without_a_rollover(self, logging_sandbox):
        os.makedirs(logging_sandbox.log_dir, exist_ok=True)
        expired = os.path.join(logging_sandbox.log_dir, backup(1))
        with gzip.open(expired, "wt", encoding="utf-8") as handle:
            handle.write("long gone")
        old = time.time() - 40 * 86400
        os.utime(expired, (old, old))

        # A file far too large to rotate: only the startup sweep can prune.
        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=10_000_000, retention_days=30)
        )

        assert backup(1) not in logging_sandbox.log_files()

    def test_the_sweep_runs_without_any_record(self, logging_sandbox, monkeypatch):
        # The interval is read when the sweep thread starts waiting, so patch it
        # before configuring.
        monkeypatch.setattr("src.main.BACKUP_SWEEP_INTERVAL_SECONDS", 0.05)
        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=10_000_000, retention_days=30)
        )
        # Created after the handler exists, so only the timer can remove it:
        # no record is logged, and the file is far too large to rotate.
        expired = os.path.join(logging_sandbox.log_dir, backup(1))
        with gzip.open(expired, "wt", encoding="utf-8") as handle:
            handle.write("long gone")
        old = time.time() - 40 * 86400
        os.utime(expired, (old, old))

        deadline = time.time() + 3
        while time.time() < deadline and os.path.exists(expired):
            time.sleep(0.02)

        assert not os.path.exists(expired)

    def test_size_is_measured_in_bytes_not_characters(self, logging_sandbox):
        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=1000, backup_count=2)
        )
        # 400 wide characters are ~1200 UTF-8 bytes: the line does not fit, even
        # though its character count does.
        logging.info("汉" * 400)
        logging.info("second line")

        files = logging_sandbox.log_files()
        assert backup(1) in files
        assert "second line" in files[LOG_FILE_NAME]

    @pytest.mark.skipif(os.name != "nt", reason="Windows locks an open file against rename")
    def test_a_locked_backup_does_not_stop_logging(self, logging_sandbox):
        os.makedirs(logging_sandbox.log_dir, exist_ok=True)
        locked = os.path.join(logging_sandbox.log_dir, backup(1))
        with gzip.open(locked, "wt", encoding="utf-8") as handle:
            handle.write("held open by a backup tool")
        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=1000, backup_count=3)
        )

        with open(locked, "rb"):
            write_lines(4)

        # The rollover could not move the locked file, so it kept a plain backup
        # and carried on: the new records still reach the log.
        assert "line-" in logging_sandbox.log_files()[LOG_FILE_NAME]

    def test_backups_are_capped_at_backup_count(self, logging_sandbox):
        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=1000, backup_count=3)
        )
        write_lines(60)

        files = logging_sandbox.log_files()
        assert sorted(files) == [LOG_FILE_NAME, backup(1), backup(2), backup(3)]

    def test_backups_stay_plain_text_when_compression_is_off(self, logging_sandbox):
        configure_logging(
            config_for(
                logging_sandbox.log_dir, max_bytes=1000, backup_count=2, compress=False
            )
        )
        write_lines(4)

        files = logging_sandbox.log_files()
        assert backup(1, compressed=False) in files
        assert not [name for name in files if name.endswith(".gz")]
        assert f"{LOG_PREFIX}0000" in files[backup(1, compressed=False)]

    def test_no_backups_are_kept_when_rotation_is_off(self, logging_sandbox):
        configure_logging(
            config_for(
                logging_sandbox.log_dir, max_bytes=1000, backup_count=5, rotate=False
            )
        )
        write_lines(12)

        files = logging_sandbox.log_files()
        assert list(files) == [LOG_FILE_NAME]
        assert len(files[LOG_FILE_NAME]) > 1000

    def test_expired_backups_are_dropped_after_a_rollover(self, logging_sandbox):
        configure_logging(
            config_for(
                logging_sandbox.log_dir, max_bytes=1000, backup_count=2, retention_days=30
            )
        )
        write_lines(3)

        expired = os.path.join(logging_sandbox.log_dir, backup(1))
        old = time.time() - 40 * 86400
        os.utime(expired, (old, old))

        # Exactly one more rollover: the aged backup shifts to .2.gz and is purged,
        # while the fresh backup stays.
        write_lines(3)
        files = logging_sandbox.log_files()

        assert backup(1) in files
        assert backup(2) not in files

    def test_retention_zero_keeps_every_backup_the_count_allows(self, logging_sandbox):
        configure_logging(
            config_for(
                logging_sandbox.log_dir, max_bytes=1000, backup_count=4, retention_days=0
            )
        )
        write_lines(40)

        aged = os.path.join(logging_sandbox.log_dir, backup(1))
        old = time.time() - 400 * 86400
        os.utime(aged, (old, old))
        write_lines(12)

        files = logging_sandbox.log_files()
        assert backup(4) in files


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

    def test_an_unusable_app_folder_fails_clearly(self, tmp_path):
        blocker = os.path.join(str(tmp_path), "not-a-folder")
        with open(blocker, "w") as handle:
            handle.write("I am a file")

        with pytest.raises(OSError):
            enter_app_dir(os.path.join(blocker, "app"))

    def test_startup_exits_with_a_message_when_the_app_folder_is_unusable(
        self, tmp_path, capsys
    ):
        blocker = os.path.join(str(tmp_path), "not-a-folder")
        with open(blocker, "w") as handle:
            handle.write("I am a file")
        paths = SimpleNamespace(
            app_dir=os.path.join(blocker, "app"), log_dir=None, settings_path=None
        )

        with pytest.raises(SystemExit) as excinfo:
            enter_app_folder_and_start_logging(paths)

        assert excinfo.value.code == 1
        assert "cannot use the app folder" in capsys.readouterr().out

    def test_a_writable_app_folder_leaves_no_probe_file(self, tmp_path, monkeypatch):
        target = os.path.join(str(tmp_path), "app")
        monkeypatch.chdir(tmp_path)

        enter_app_dir(target)

        assert os.listdir(target) == []

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

    def test_startup_moves_into_the_app_folder_and_starts_logging_there(
        self, tmp_path, monkeypatch, logging_sandbox
    ):
        chosen = os.path.join(str(tmp_path), "app")
        args = SimpleNamespace(appDir=chosen, logDir=None, settings=None)
        monkeypatch.chdir(tmp_path)

        app_dir, config = enter_app_folder_and_start_logging(resolve_startup_paths(args))

        assert os.path.normpath(app_dir) == os.path.normpath(chosen)
        assert os.path.normpath(os.getcwd()) == os.path.normpath(chosen)
        assert os.path.normpath(config.log_dir) == os.path.normpath(
            os.path.join(chosen, "logs")
        )
        # The folder is the first thing in the log, so it can be found later.
        with open(
            os.path.join(chosen, "logs", LOG_FILE_NAME), encoding="utf-8"
        ) as handle:
            assert f"App folder: {chosen}" in handle.read()

    def test_app_folder_flag_is_parsed_and_not_applied_as_a_setting(self, tmp_path):
        chosen = os.path.join(str(tmp_path), "app")
        settings = Settings()

        apply_overrides(parse_args(["--app-dir", chosen]), settings)

        assert settings.get("appDir") is None

    def test_relative_flags_are_resolved_where_the_user_typed_them(
        self, tmp_path, monkeypatch
    ):
        shell = os.path.join(str(tmp_path), "shell")
        os.makedirs(shell)
        monkeypatch.chdir(shell)
        args = SimpleNamespace(appDir="app", logDir="mylogs", settings="conf/prod.yaml")

        paths = resolve_startup_paths(args)

        assert os.path.normpath(paths.app_dir) == os.path.normpath(
            os.path.join(shell, "app")
        )
        assert os.path.normpath(paths.log_dir) == os.path.normpath(
            os.path.join(shell, "mylogs")
        )
        assert os.path.normpath(paths.settings_path) == os.path.normpath(
            os.path.join(shell, "conf", "prod.yaml")
        )

    def test_a_relative_log_dir_means_one_folder_in_both_phases(
        self, tmp_path, monkeypatch, logging_sandbox
    ):
        shell = os.path.join(str(tmp_path), "shell")
        os.makedirs(shell)
        monkeypatch.chdir(shell)
        args = SimpleNamespace(appDir="app", logDir="mylogs", settings=None)
        paths = resolve_startup_paths(args)

        app_dir, active = enter_app_folder_and_start_logging(paths)
        # What apply_overrides does with the (already resolved) flag.
        settings = Settings()
        settings["logDir"] = paths.log_dir
        configured = _log_config(settings, app_dir, cli_log_dir=paths.log_dir)

        expected = os.path.join(shell, "mylogs")
        assert os.path.normpath(active.log_dir) == os.path.normpath(expected)
        assert os.path.normpath(configured.log_dir) == os.path.normpath(expected)

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


class TestLogPathResolution:
    """Logs live in the app folder: `logs/` under it, never left to chance."""

    def test_logs_follow_the_app_folder(self, tmp_path, monkeypatch):
        dist = os.path.join(str(tmp_path), "dist")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", os.path.join(dist, "palworld-monitor.exe"))

        assert os.path.normpath(_resolve_log_dir(None, resolve_app_dir())) == os.path.normpath(
            os.path.join(dist, "logs")
        )

    def test_source_run_logs_at_the_project_root_not_under_src(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)

        log_dir = os.path.normpath(_resolve_log_dir(None, resolve_app_dir()))

        assert log_dir == os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "logs")
        )
        assert os.path.basename(os.path.dirname(log_dir)) != "src"

    def test_logs_ignore_the_shell_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        isolate_app_location(tmp_path, monkeypatch)
        before = _resolve_log_dir(None, resolve_app_dir())
        monkeypatch.chdir(tmp_path)

        assert _resolve_log_dir(None, resolve_app_dir()) == before

    def test_configured_log_dir_is_absolute_and_expands_user(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        expanded = _resolve_log_dir("~/palworld-logs", str(tmp_path))
        assert os.path.isabs(expanded)
        assert expanded == os.path.join(os.path.expanduser("~"), "palworld-logs")
        assert "~" not in expanded
        assert os.path.normpath(_resolve_log_dir("logs", str(tmp_path))) == os.path.normpath(
            os.path.join(str(tmp_path), "logs")
        )

    def test_cli_log_dir_wins_over_the_default(self, tmp_path):
        custom = os.path.join(str(tmp_path), "custom-logs")
        config = _log_config(None, str(tmp_path), cli_log_dir=custom)

        assert os.path.normpath(config.log_dir) == os.path.normpath(custom)


class TestZeroConfigDefaults:
    """A normal install needs no logging settings at all, and is bounded."""

    def test_defaults_come_from_the_schema_without_any_config(self, tmp_path):
        settings = Settings()
        config = _log_config(settings, str(tmp_path))

        assert os.path.normpath(config.log_dir) == os.path.normpath(
            os.path.join(str(tmp_path), "logs")
        )
        assert config.max_bytes == parse_size(DEFAULT_LOG_SETTINGS["logMaxSize"])
        assert config.max_bytes == 5 * 1024 * 1024
        assert config.backup_count == DEFAULT_LOG_SETTINGS["logBackupCount"]
        assert config.rotate is True
        assert config.compress is True
        assert config.retention_days == DEFAULT_LOG_SETTINGS["logRetentionDays"]

    def test_bootstrap_before_settings_exist_uses_the_defaults(self, tmp_path):
        config = _log_config(None, str(tmp_path))

        assert config.max_bytes == 5 * 1024 * 1024
        assert config.backup_count == 10
        assert config.retention_days == 30
        assert os.path.normpath(config.log_dir) == os.path.normpath(
            os.path.join(str(tmp_path), "logs")
        )

    def test_logging_section_of_settings_file_is_applied(self, tmp_path):
        custom_dir = os.path.join(str(tmp_path), "my-logs")
        payload = {
            "logging": {
                "dir": custom_dir,
                "maxSize": "4 KB",
                "backupCount": 4,
                "rotate": True,
                "compress": False,
                "retentionDays": 7,
            }
        }
        settings_path = os.path.join(str(tmp_path), "settings.yaml")
        with open(settings_path, "w") as handle:
            yaml.safe_dump(payload, handle)

        settings = Settings()
        settings.readSettings(settings_path)
        config = _log_config(settings, str(tmp_path))

        assert os.path.normpath(config.log_dir) == os.path.normpath(custom_dir)
        assert (config.max_bytes, config.backup_count, config.compress) == (
            4096,
            4,
            False,
        )
        assert config.retention_days == 7

    def test_malformed_log_settings_fall_back_to_defaults_and_are_reported(self, tmp_path):
        settings = Settings()
        settings["logMaxSize"] = "five megabytes"
        settings["logRetentionDays"] = None

        # Bootstrap logging must not fail on a bad value; validation reports it.
        config = _log_config(settings, str(tmp_path))
        assert config.max_bytes == parse_size(DEFAULT_LOG_SETTINGS["logMaxSize"])
        assert config.retention_days == DEFAULT_LOG_SETTINGS["logRetentionDays"]

        with pytest.raises(ValueError, match="logMaxSize"):
            settings.validate_settings()

    def test_cli_flag_is_applied_as_a_settings_override(self, tmp_path):
        custom_dir = os.path.join(str(tmp_path), "cli-logs")
        settings = Settings()
        apply_overrides(parse_args(["--log-dir", custom_dir]), settings)
        config = _log_config(settings, str(tmp_path))

        assert os.path.normpath(config.log_dir) == os.path.normpath(custom_dir)


class TestInvalidLogSettings:
    """Bad values are reported by validation and never crash startup."""

    def test_infinite_values_are_reported_and_fall_back(self, tmp_path):
        settings = Settings()
        settings["logMaxSize"] = float("inf")
        settings["logBackupCount"] = float("inf")

        # Bootstrap must not fail: the defaults are used until validation runs.
        config = _log_config(settings, str(tmp_path))
        assert config.max_bytes == parse_size(DEFAULT_LOG_SETTINGS["logMaxSize"])
        assert config.backup_count == DEFAULT_LOG_SETTINGS["logBackupCount"]

        with pytest.raises(ValueError) as excinfo:
            settings.validate_settings()
        assert "logMaxSize" in str(excinfo.value)
        assert "logBackupCount" in str(excinfo.value)

    def test_absurd_backup_count_is_rejected(self):
        settings = Settings()
        settings["logBackupCount"] = MAX_LOG_BACKUP_COUNT + 1

        with pytest.raises(ValueError, match="logBackupCount"):
            settings.validate_settings()

    def test_rotation_flags_must_be_booleans(self):
        settings = Settings()
        settings["logRotate"] = "false"
        settings["logCompress"] = 0

        with pytest.raises(ValueError) as excinfo:
            settings.validate_settings()
        assert "logRotate" in str(excinfo.value)
        assert "logCompress" in str(excinfo.value)

    def test_an_absurd_backup_count_is_clamped_before_validation(self, tmp_path):
        settings = Settings()
        settings["logBackupCount"] = 10**8

        # Bootstrap configures logging before validation runs: the handler must
        # never see a count that large.
        assert _log_config(settings, str(tmp_path)).backup_count == MAX_LOG_BACKUP_COUNT

    def test_log_dir_must_be_a_path(self):
        settings = Settings()
        settings["logDir"] = 5

        with pytest.raises(ValueError, match="logDir"):
            settings.validate_settings()

class TestLevelAndFallback:
    """LOG_LEVEL keeps working, and logging never takes the app down."""

    def test_log_level_from_the_environment_is_applied(self, logging_sandbox, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "debug")
        config = _log_config(None, logging_sandbox.log_dir)

        assert config.level == "DEBUG"
        configure_logging(config_for(logging_sandbox.log_dir, level="DEBUG"))
        logging.debug("verbose line")

        assert "verbose line" in logging_sandbox.log_files()[LOG_FILE_NAME]
        assert logging.getLogger().level == logging.DEBUG

    def test_unknown_log_level_falls_back_to_info(self, logging_sandbox, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "not-a-level")
        config = _log_config(None, logging_sandbox.log_dir)

        assert config.level == "NOT-A-LEVEL"
        configure_logging(config_for(logging_sandbox.log_dir, level=config.level))
        assert logging.getLogger().level == logging.INFO

    def test_unusable_log_folder_falls_back_to_console_only(self, logging_sandbox, tmp_path):
        blocker = os.path.join(str(tmp_path), "not-a-folder")
        with open(blocker, "w") as handle:
            handle.write("I am a file")

        config = config_for(os.path.join(blocker, "logs"))
        configure_logging(config)
        logging.info("console only")

        handlers = logging.getLogger().handlers
        app_handlers = [h for h in handlers if getattr(h, _APP_HANDLER_FLAG, False)]
        assert not [h for h in app_handlers if isinstance(h, logging.FileHandler)]
        assert app_handlers  # console handler is still installed
        assert "console only" in logging_sandbox.console.getvalue()
        assert "Not logging to" in logging_sandbox.console.getvalue()

    def test_missing_log_dir_setting_keeps_the_console(self, logging_sandbox):
        configure_logging(config_for(None))
        logging.info("no file")

        assert "no file" in logging_sandbox.console.getvalue()
        assert not os.path.exists(logging_sandbox.log_dir)


class TestThreadSafety:
    """The dashboard and lifecycle threads log concurrently."""

    def test_concurrent_logging_loses_no_lines(self, logging_sandbox):
        configure_logging(
            config_for(logging_sandbox.log_dir, max_bytes=20000, backup_count=8)
        )

        writers, readers = 6, 40
        errors = []

        def writer(thread_id):
            try:
                for index in range(readers):
                    logging.info(f"thread-{thread_id}-entry-{index}")
            except Exception as exc:  # pragma: no cover - the point is that this doesn't happen
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        text = "".join(logging_sandbox.log_files().values())
        for thread_id in range(writers):
            for index in range(readers):
                assert f"thread-{thread_id}-entry-{index}" in text
