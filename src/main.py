# Copyright (c) 2024 Nomomo
# Copyright (c) 2024 Kevin Perez - Modified work

import contextlib
import logging
import os
import sys
from dataclasses import dataclass

from src.cli import parse_args, apply_overrides
from src.banlist_manager import BanlistManager
from src.events import EventBus
from src.palworld_control import PalWorldController
from src.player_manager import PlayerManager
from src.process_manager import create_process_manager
from src.settings import settings
from src.setup_wizard import (
    auto_detect_palserver,
    interactive_setup,
    print_web_link,
)
from src.web_server import WebServer
from src.auto_start import AutoStartManager


SETTINGS_FILE_NAME = "settings.yaml"
APP_DIR_ENV = "PALWORLD_MONITOR_APP_DIR"
SETTINGS_ENV = "PALWORLD_MONITOR_SETTINGS"


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
class StartupPaths:
    """The paths a run needs before anything else: app folder and settings."""

    app_dir: str
    settings_path: str | None


def resolve_startup_paths(args):
    """Resolve the path flags while the process is still where the user typed them.

    Relative paths belong to the shell that ran the app, so they are resolved
    once here, before `enter_app_dir` moves the process into the app folder.
    """
    return StartupPaths(
        app_dir=resolve_app_dir(_absolute(args.appDir)),
        settings_path=_absolute(args.settings)
        or _absolute(os.environ.get(SETTINGS_ENV)),
    )


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
    try:
        enter_app_dir(paths.app_dir)
    except OSError as exc:
        print(f"error: cannot use the app folder {paths.app_dir}: {exc}")
        print("point --app-dir at a folder you can write to, then restart.")
        sys.exit(1)

    log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(paths.app_dir, "app.log"), mode="w"
            ),
        ],
    )
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    settings_path, settings_note = _determine_settings_path(
        paths.settings_path, paths.app_dir
    )
    if settings_note:
        logging.warning(settings_note)

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
