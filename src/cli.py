"""CLI argument parsing and application to settings."""

import argparse
import logging

from src.settings import settings as _settings

_sensitive = {"webPassword", "palworldServerAdminPassword", "sessionSecretKey"}


def build_parser():
    """Return an ArgumentParser for palworld-monitor CLI flags."""
    parser = argparse.ArgumentParser(
        description="Palworld dedicated server monitor with auto start/stop and web admin."
    )
    parser.add_argument("--settings", metavar="PATH", help="Path to settings.yaml")
    parser.add_argument(
        "--app-dir", dest="appDir", metavar="PATH",
        help="Folder the app keeps its files in: settings.yaml, logs, player data "
        "(default: the folder holding the exe, the project root from source)",
    )

    # Palworld server
    parser.add_argument(
        "--exe-path", dest="palworldServerExePath", metavar="PATH", help="Path to PalServer.exe"
    )
    parser.add_argument(
        "--admin-password", dest="palworldServerAdminPassword", metavar="PASS",
        help="Palworld REST admin password",
    )
    parser.add_argument(
        "--host", dest="palworldServerHost", metavar="HOST", help="Palworld server host"
    )
    parser.add_argument(
        "--server-port", dest="palworldServerPort", type=int, metavar="PORT",
        help="Palworld game server port",
    )
    parser.add_argument(
        "--protocol", choices=["REST"], metavar="PROTO",
        help="Server communication protocol",
    )
    parser.add_argument(
        "--polling-rate", dest="pollingRate", type=int, metavar="SEC",
        help="Seconds between status polls",
    )
    parser.add_argument(
        "--use-lgsm", dest="useLGSM", action="store_true", default=None,
        help="Start/stop through a LinuxGSM script (--exe-path points to the LGSM script) "
        "instead of launching PalServer directly",
    )

    # Web server
    parser.add_argument(
        "--no-web", dest="useWebServer", action="store_false", default=None,
        help="Disable the web admin UI",
    )
    parser.add_argument(
        "--web-port", dest="webServerPort", type=int, metavar="PORT",
        help="Web admin UI port",
    )
    parser.add_argument(
        "--web-username", dest="webUsername", metavar="USER", help="Web admin username"
    )
    parser.add_argument(
        "--web-password", dest="webPassword", metavar="PASS", help="Web admin password"
    )

    # Features
    parser.add_argument(
        "--no-auto-start", dest="autoStart", action="store_false", default=None,
        help="Disable auto-start on UDP probe",
    )
    parser.add_argument(
        "--no-auto-stop", dest="autoStop", action="store_false", default=None,
        help="Disable auto-stop when server is empty",
    )

    return parser


def parse_args(argv=None):
    """Parse CLI arguments.  *argv* overrides sys.argv for testing."""
    return build_parser().parse_args(argv)


def apply_overrides(args, settings=None):
    """Apply non-None CLI args as settings overrides.

    `settings` and `appDir` are where the app finds its files, not settings
    values, so they are not applied. *settings* defaults to the global settings
    singleton.
    """
    if settings is None:
        settings = _settings

    for key, value in vars(args).items():
        if value is not None and key not in ("settings", "appDir"):
            settings[key] = value
            display = "***" if key in _sensitive else repr(value)
            logging.info(f"CLI override: {key}={display}")
