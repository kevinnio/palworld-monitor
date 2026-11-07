# Copyright (c) 2024 Nomomo
# Copyright (c) 2024 Kevin Perez - Modified work

import contextlib
import gzip
import logging
import logging.handlers
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass

from src.cli import parse_args, apply_overrides
from src.banlist_manager import BanlistManager
from src.events import EventBus
from src.palworld_control import PalWorldController
from src.player_manager import PlayerManager
from src.process_manager import create_process_manager
from src.settings import (
    DEFAULT_LOG_SETTINGS,
    MAX_LOG_BACKUP_COUNT,
    MAX_LOG_RETENTION_DAYS,
    parse_size,
    settings,
)
from src.setup_wizard import (
    auto_detect_palserver,
    interactive_setup,
    print_web_link,
)
from src.web_server import WebServer
from src.auto_start import AutoStartManager

LOG_FILE_NAME = "palworld-monitor.log"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
SETTINGS_FILE_NAME = "settings.yaml"
LOG_DIR_NAME = "logs"
DEFAULT_LOG_LEVEL = "INFO"
APP_DIR_ENV = "PALWORLD_MONITOR_APP_DIR"
SETTINGS_ENV = "PALWORLD_MONITOR_SETTINGS"
# Backups are swept at startup, after every rollover, and at most this often in
# between, so a quiet server still prunes what it stopped rotating long ago.
BACKUP_SWEEP_INTERVAL_SECONDS = 3600
# A failed rollover (a backup held open by an antivirus or backup tool, a full
# disk) is retried after this pause, not on every single record.
ROTATION_RETRY_SECONDS = 60
# Marks the handlers this module installs, so reconfiguring logging replaces
# only its own handlers and leaves any other handler (tests, a hosting app) alone.
_APP_HANDLER_FLAG = "palworld_monitor_handler"


def _absolute(path):
    """Resolve a user-supplied path: `~` expanded, relative to the current folder.

    Returns None for an unset or blank value, so callers keep their own defaults.
    """
    if not path:
        return None
    text = str(path).strip()
    if not text:
        return None
    return os.path.abspath(os.path.expanduser(text))


def resolve_app_dir(cli_app_dir=None):
    """The folder the app owns: its settings, logs, player data, and pid files.

    Defaults to the folder holding the exe in a release and the project root
    when running from source — `src/` is source code, and a user's settings file
    is not, so it sits with the app in both layouts. `--app-dir` and the
    PALWORLD_MONITOR_APP_DIR environment move it for hosts that keep the app
    and its data apart (a LinuxGSM instance folder, a data partition).
    """
    configured = _absolute(cli_app_dir) or _absolute(os.environ.get(APP_DIR_ENV))
    if configured:
        return configured
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def enter_app_dir(app_dir):
    """Create the app folder, move the process into it, and check it is writable.

    The app's own files are written by relative path, so one `chdir` at startup
    is what makes "everything lives in the app folder" true for all of them. A
    folder that exists but is read-only would start with console-only logs and
    silently failed player saves, so it is rejected here instead.
    """
    os.makedirs(app_dir, exist_ok=True)
    os.chdir(app_dir)
    _ensure_writable(app_dir)


def _ensure_writable(app_dir):
    """Raise `OSError` when the app cannot create files in *app_dir*."""
    probe = os.path.join(app_dir, f".write-test-{os.getpid()}")
    try:
        with open(probe, "w", encoding="utf-8"):
            pass
    finally:
        with contextlib.suppress(OSError):
            os.remove(probe)


def _determine_settings_path(explicit_settings, app_dir):
    """Return `(settings_path, note)` for the settings file: `settings.yaml` in the app folder.

    `--settings` and PALWORLD_MONITOR_SETTINGS override it, already resolved by
    `resolve_startup_paths`. A settings file left in `src/` by an older install
    is still read, and *note* then tells the user to move it, so the app never
    silently starts with defaults.
    """
    if explicit_settings:
        return explicit_settings, None

    settings_path = os.path.join(app_dir, SETTINGS_FILE_NAME)
    legacy = os.path.abspath(os.path.join(os.path.dirname(__file__), SETTINGS_FILE_NAME))
    missing = not os.path.exists(settings_path)
    if legacy != os.path.abspath(settings_path) and missing and os.path.exists(legacy):
        return legacy, f"Reading {legacy}; move it to {settings_path} and restart."
    return settings_path, None


@dataclass(frozen=True)
class LogConfig:
    """Effective logging setup: where to write, at what level, and how to cap it."""

    log_dir: str | None
    level: str
    max_bytes: int
    backup_count: int
    rotate: bool
    compress: bool
    retention_days: int


def _resolve_log_dir(configured_dir, app_dir):
    """Return the absolute folder that holds log files: `logs/` in the app folder."""
    return _absolute(configured_dir) or os.path.join(app_dir, LOG_DIR_NAME)


def _as_int(value, default):
    """Read an integer setting, falling back to *default* when it isn't a number.

    A malformed value is reported by `validate_settings`; bootstrap must not
    fail on it.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_size(value, default):
    """Read a log size setting, falling back to *default* when it is unparseable."""
    try:
        return parse_size(value)
    except (TypeError, ValueError, OverflowError):
        return parse_size(default)


def _log_config(settings_obj, app_dir, cli_log_dir=None):
    """Build the LogConfig to apply.

    Reads the settings layer (CLI overrides are already applied to it) and falls
    back to DEFAULT_LOG_SETTINGS for anything unset. *settings_obj* is None
    during bootstrap, before settings.yaml has been read, when only the CLI flags
    are known.
    """
    get = settings_obj.get if settings_obj is not None else DEFAULT_LOG_SETTINGS.get
    backup_count = _as_int(
        get("logBackupCount"), DEFAULT_LOG_SETTINGS["logBackupCount"]
    )
    retention_days = _as_int(
        get("logRetentionDays"), DEFAULT_LOG_SETTINGS["logRetentionDays"]
    )
    return LogConfig(
        log_dir=_resolve_log_dir(cli_log_dir or get("logDir"), app_dir),
        level=os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper(),
        max_bytes=_as_size(get("logMaxSize"), DEFAULT_LOG_SETTINGS["logMaxSize"]),
        # Validation reports out-of-range values, but bootstrap runs before it:
        # clamp here so a typo cannot make the first rollover walk a huge range.
        backup_count=max(0, min(backup_count, MAX_LOG_BACKUP_COUNT)),
        rotate=bool(get("logRotate")),
        compress=bool(get("logCompress")),
        retention_days=max(0, min(retention_days, MAX_LOG_RETENTION_DAYS)),
    )


class _GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Size-based rotation with gzipped backups and age-based cleanup.

    Backups are `palworld-monitor.log.1.gz`, `.2.gz`, ... A `backup_count` of 0
    keeps only the active file: it starts over at `max_bytes` and its history is
    dropped. Backups left in the plain `.N` form by a run with compression off
    are shifted and pruned alongside the gzipped ones.

    The sweep drops backups past the count cap or older than `retention_days`.
    It runs at startup, after every rollover, and hourly in between, so a quiet
    server still prunes files it stopped rotating weeks ago. Rotation runs under
    the handler lock the logging module takes around `emit`, so it is safe with
    the dashboard and lifecycle threads writing concurrently.
    """

    # Set by `_release_app_handlers`: a stale reference must not reopen the file.
    closing = False

    def __init__(self, *args, retention_days=0, compress=True, rotate=True, **kwargs):
        super().__init__(*args, **kwargs)
        # A typo in the count must not make the first rollover walk millions of
        # names; validation reports the value, logging stays usable meanwhile.
        self.backupCount = max(0, min(int(self.backupCount), MAX_LOG_BACKUP_COUNT))
        self.retention_days = max(0, min(int(retention_days), MAX_LOG_RETENTION_DAYS))
        self.compress = bool(compress)
        self.rotate = bool(rotate)
        self._rotation_blocked_until = 0.0
        self._prune_backups()
        # Age out backups even when the app is quiet: no record, no rollover, but
        # the old files still need to go.
        self._sweep_stop = threading.Event()
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop, name="palworld-log-sweep", daemon=True
        )
        self._sweep_thread.start()

    def close(self):
        """Stop the sweep thread, then close the file.

        The thread is joined before the file is closed, so it cannot prune
        against a handler that is shutting down.
        """
        self._sweep_stop.set()
        self._sweep_thread.join(timeout=5)
        super().close()

    def emit(self, record):
        """Never reopen a file that `_release_app_handlers` already closed."""
        if self.closing:
            return
        super().emit(record)

    def _sweep_loop(self):
        while not self._sweep_stop.wait(BACKUP_SWEEP_INTERVAL_SECONDS):
            with self.lock:
                self._prune_backups()

    def shouldRollover(self, record):
        """Roll when *this* record would push the whole file past maxBytes."""
        if self.closing:
            return False
        if self.stream is None:
            if self.delay:
                return False
            self.stream = self._open()
        if not self.rotate or self.maxBytes <= 0:
            return False
        if time.monotonic() < self._rotation_blocked_until:
            return False
        try:
            # Append mode leaves the stream at the end of the file, so this is
            # everything already on disk, not just what this run wrote.
            written = self.stream.tell()
        except (OSError, ValueError):  # a stream without a position
            return False
        if written == 0:  # never roll over an empty file
            return False
        message = f"{self.format(record)}\n"
        return written + len(message.encode(self.encoding or "utf-8")) >= self.maxBytes

    def doRollover(self):
        try:
            self._rotate_files()
        except OSError as exc:
            # Keep writing to the file we have, and retry after a pause instead
            # of failing (and losing a record) on every single line.
            self._rotation_blocked_until = time.monotonic() + ROTATION_RETRY_SECONDS
            self._reopen()
            print(f"warning: cannot rotate {self.baseFilename}: {exc}", file=sys.stderr)
        else:
            self._rotation_blocked_until = 0.0
            self._prune_backups()

    def _rotate_files(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        try:
            if self.backupCount > 0:
                self._shift_backups()
                self._move_active_to_first_backup()
            else:
                self._truncate_active()
        finally:
            if not self.delay:
                self._reopen()

    def _shift_backups(self):
        """Shift `.N` and `.N.gz` up by one; a locked file is left for the next try."""
        for index in range(self.backupCount - 1, 0, -1):
            for suffix in ("", ".gz"):
                source = f"{self.baseFilename}.{index}{suffix}"
                if os.path.exists(source):
                    try:
                        os.replace(source, f"{self.baseFilename}.{index + 1}{suffix}")
                    except OSError:
                        continue

    def _move_active_to_first_backup(self):
        first = f"{self.baseFilename}.1"
        if self.compress:
            try:
                self._gzip_active_to(f"{first}.gz")
            except OSError:
                pass  # a plain backup beats losing the history
            else:
                os.remove(self.baseFilename)
                return
        os.replace(self.baseFilename, first)

    def _gzip_active_to(self, dest):
        """Write the compressed copy under a temp name, then swap it in whole."""
        temp = f"{dest}.tmp"
        try:
            with open(self.baseFilename, "rb") as plain, gzip.open(temp, "wb") as compressed:
                shutil.copyfileobj(plain, compressed)
            os.replace(temp, dest)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(temp)
            raise

    def _truncate_active(self):
        """`backup_count: 0`: start the active file over and drop its history."""
        with open(self.baseFilename, "w", encoding=self.encoding or "utf-8"):
            pass

    def _reopen(self):
        if self.stream is None and not self.closing:
            self.stream = self._open()

    def _prune_backups(self):
        """Keep the newest `backupCount` backups, and drop the expired ones.

        Counting files rather than indices is what bounds the folder when plain
        and gzipped backups coexist after a compression toggle: two files can
        share an index, and both count against the cap.
        """
        directory, base = os.path.split(self.baseFilename)
        cutoff = None
        if self.retention_days > 0:
            cutoff = time.time() - self.retention_days * 86400
        try:
            entries = os.listdir(directory)
        except OSError:
            return

        backups = []
        for entry in entries:
            index = self._backup_index(entry, base)
            if index is None:
                continue
            path = os.path.join(directory, entry)
            try:
                backups.append((index, os.path.getmtime(path), path))
            except OSError:
                continue

        # A lower index is newer; at the same index the newer file wins.
        backups.sort(key=lambda backup: (backup[0], -backup[1]))
        kept = 0
        for _index, mtime, path in backups:
            expired = cutoff is not None and mtime < cutoff
            # Rotation off means no new backups: the old ones only age out.
            within_count = not self.rotate or kept < self.backupCount
            if not expired and within_count:
                kept += 1
            else:
                with contextlib.suppress(OSError):
                    os.remove(path)

    @staticmethod
    def _backup_index(entry, base):
        """The N in `<base>.N` or `<base>.N.gz`, or None for any other file."""
        prefix = f"{base}."
        if not entry.startswith(prefix):
            return None
        suffix = entry[len(prefix) :]
        if suffix.endswith(".gz"):
            suffix = suffix[: -len(".gz")]
        return int(suffix) if suffix.isdigit() else None


def _log_level(name):
    """Resolve a level name to a logging level, defaulting to INFO."""
    return getattr(logging, str(name).upper(), logging.INFO)


def _release_app_handlers():
    """Remove and close the handlers installed by a previous call.

    Removing first stops new records from routing here. Closing then waits for
    any `emit` already running on another thread (the handler's own lock) before
    the file is closed. A reference captured earlier still exists, so the
    handlers are marked `closing` and go quiet instead of reopening the file.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _APP_HANDLER_FLAG, False):
            root.removeHandler(handler)
            handler.closing = True
            handler.close()


class _AppStreamHandler(logging.StreamHandler):
    """Console handler that stays quiet once it has been released."""

    closing = False

    def emit(self, record):
        if self.closing:
            return
        super().emit(record)


def _build_file_handler(config, level):
    """Open the rotating log file, or return None if it can't be written."""
    if not config.log_dir:
        return None
    try:
        os.makedirs(config.log_dir, exist_ok=True)
        handler = _GzipRotatingFileHandler(
            os.path.join(config.log_dir, LOG_FILE_NAME),
            mode="a",  # append: history survives restarts and crashes
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
            rotate=config.rotate,
            retention_days=config.retention_days,
            compress=config.compress,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.warning(f"Not logging to {config.log_dir}: {exc}")
        return None

    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _APP_HANDLER_FLAG, True)
    return handler


def configure_logging(config):
    """Install console + log-file handlers on the root logger, and return what is active.

    Safe to call repeatedly: handlers from an earlier call are closed first, and
    the log file is opened in append mode, so reconfiguring never truncates. If
    the log folder can't be created (read-only install, no permission), logging
    continues on the console alone — the app never fails because of logging.
    """
    root = logging.getLogger()
    level = _log_level(config.level)

    _release_app_handlers()

    console = _AppStreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(console, _APP_HANDLER_FLAG, True)
    root.addHandler(console)
    root.setLevel(level)

    file_handler = _build_file_handler(config, level)
    if file_handler is not None:
        root.addHandler(file_handler)

    return config


@dataclass(frozen=True)
class StartupPaths:
    """The paths a run needs before anything else: app folder, logs, settings."""

    app_dir: str
    log_dir: str | None
    settings_path: str | None


def resolve_startup_paths(args):
    """Resolve the path flags while the process is still where the user typed them.

    Relative paths belong to the shell that ran the app, so they are resolved
    once here, before `enter_app_dir` moves the process into the app folder.
    """
    return StartupPaths(
        app_dir=resolve_app_dir(_absolute(args.appDir)),
        log_dir=_absolute(args.logDir),
        settings_path=_absolute(args.settings)
        or _absolute(os.environ.get(SETTINGS_ENV)),
    )


def enter_app_folder_and_start_logging(paths):
    """Move into the app folder, start logging there, and return `(app_dir, config)`.

    Logging starts before settings load, so startup and configuration errors
    reach the log file.
    """
    try:
        enter_app_dir(paths.app_dir)
    except OSError as exc:
        print(f"error: cannot use the app folder {paths.app_dir}: {exc}")
        print("point --app-dir at a folder you can write to, then restart.")
        sys.exit(1)

    config = _log_config(None, paths.app_dir, cli_log_dir=paths.log_dir)
    configure_logging(config)
    # The first line anyone looks for when they can't find their files.
    logging.info(f"App folder: {paths.app_dir}")
    return paths.app_dir, config


def main():
    args = parse_args()

    print(
        r"""
 ____   _    _ __        _____  ____  _     ____
|  _ \ / \  | |\ \      / / _ \|  _ \| |   |  _ \
| |_) / _ \ | | \ \ /\ / / | | | |_) | |   | | | |
|  __/ ___ \| |__\ V  V /| |_| |  _ <| |___| |_| |
|_| /_/   \_\_____\_/\_/  \___/|_| \_\_____|____/

 __  __  ___  _   _ ___ _____ ___  ____
|  \/  |/ _ \| \ | |_ _|_   _/ _ \|  _ \
| |\/| | | | |  \| || |  | || | | | |_) |
| |  | | |_| | |\  || |  | || |_| |  _ <
|_|  |_|\___/|_| \_|___| |_| \___/|_| \_\
""",
        flush=True,
    )

    paths = resolve_startup_paths(args)
    # apply_overrides reads the raw flags; give it the resolved log folder so a
    # relative --log-dir means one folder in both logging phases.
    args.logDir = paths.log_dir

    app_dir, active_log_config = enter_app_folder_and_start_logging(paths)
    settings_path, settings_note = _determine_settings_path(paths.settings_path, app_dir)
    if settings_note:
        logging.warning(settings_note)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    # First run: no settings file → interactive setup
    if not os.path.exists(settings_path):
        try:
            detected = auto_detect_palserver()
            interactive_setup(settings_path, detected, use_lgsm=bool(args.useLGSM))
        except EOFError:
            print("error: no terminal available for interactive setup.")
            print(
                f"copy src/settings.yaml.example -> {settings_path} and edit it, then restart."
            )
            sys.exit(1)

    logging.info(f"Loading settings from {settings_path}")
    settings.readSettings(settings_path)

    # Apply CLI flag overrides (after file, before validation)
    apply_overrides(args)

    # Logging settings are part of the config, so switch to them now that the
    # file and CLI overrides are known. Same values, same handlers: nothing to do.
    configured_log_config = _log_config(settings, app_dir, cli_log_dir=paths.log_dir)
    if configured_log_config != active_log_config:
        configure_logging(configured_log_config)

    # Validate settings at startup
    try:
        settings.validate_settings()
    except ValueError as e:
        logging.error(e)
        logging.error("Please fix the settings and try with valid configuration.")
        sys.exit(1)

    # Client selection
    if settings.protocol.upper() == "REST":
        from src.api_clients import RestClient

        client = RestClient()
    else:
        logging.error(f"Invalid protocol specified in settings: {settings.protocol}")
        sys.exit(1)

    bus = EventBus()
    process_manager = create_process_manager()
    palworld_controller = PalWorldController(
        client=client,
        process_manager=process_manager,
        player_manager=PlayerManager(bus=bus),
        banlist_manager=BanlistManager(bus=bus),
        bus=bus,
    )
    palworld_controller.start()

    auto_start_manager = None
    if settings.autoStart:
        auto_start_manager = AutoStartManager(palworld_controller, bus=bus)
        # The supervisor reports STOPPED until its first scan; never bind the
        # game port while an undiscovered server may already hold it.
        if process_manager.find_process_identity("PalServer") is None:
            auto_start_manager.listen_palworld_access()

    web_server = None
    try:
        if settings.useWebServer:
            print_web_link(settings.webServerPort)
            web_server = WebServer(palworld_controller, bus=bus)
            web_server.run()
    finally:
        if web_server is not None:
            web_server.close()
        if auto_start_manager is not None:
            auto_start_manager.close()
        palworld_controller.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Error from src.main routine")
        sys.exit(1)
