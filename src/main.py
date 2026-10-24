# Copyright (c) 2024 Nomomo
# Copyright (c) 2024 Kevin Perez - Modified work

import logging
import os
import sys
import traceback

from src.cli import parse_args, apply_overrides
from src.palworld_control import PalWorldController
from src.settings import settings
from src.setup_wizard import (
    auto_detect_palserver,
    interactive_setup,
    print_web_link,
)
from src.web_server import WebServer
from src.auto_start import AutoStartManager


def _determine_settings_path(args):
    """Return the path to settings.yaml, accounting for frozen mode and CLI."""
    if getattr(sys, "frozen", False):
        default = os.path.join(os.path.dirname(sys.executable), "settings.yaml")
    else:
        default = os.path.join(os.path.dirname(__file__), "settings.yaml")
    return args.settings or os.environ.get("PALWORLD_MONITOR_SETTINGS", default)


def main():
    args = parse_args()

    print(r"""
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
""", flush=True)

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("app.log", mode="w"),
        ],
    )
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    settings_path = _determine_settings_path(args)

    # First run: no settings file → interactive setup
    if not os.path.exists(settings_path):
        try:
            detected = auto_detect_palserver()
            interactive_setup(settings_path, detected, use_lgsm=bool(args.useLGSM))
        except EOFError:
            print("error: no terminal available for interactive setup.")
            print(f"copy src/settings.yaml.example -> {settings_path} and edit it, then restart.")
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

    palworld_controller = PalWorldController(client)

    # Start background update thread if server is running
    server_running = palworld_controller.is_palworld_process_running()
    if server_running:
        palworld_controller.start_server_info_update_thread()

    if settings.autoStart:
        auto_start_manager = AutoStartManager(palworld_controller)
        if not server_running:
            auto_start_manager.listen_palworld_access()

    if settings.useWebServer:
        print_web_link(settings.webServerPort)
        web_server = WebServer(palworld_controller)
        web_server.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"Error from src.main routine: {e}")
        logging.error(traceback.format_exc())
        sys.exit(1)
