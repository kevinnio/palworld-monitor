# Copyright (c) 2024 Nomomo
# Copyright (c) 2026 Kevin Perez - Modified work
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

import yaml
import logging
import math
import os
import re
import shutil
import subprocess
import secrets

PALWORLD_EXE_ARGUMENTS = (
    "-useperfthreads -NoAsyncLoadingThread -UseMultithreadForDS "
    "-NumberOfWorkerThreadsServer=16 -restapi"
)

DEFAULT_SECURITY_SETTINGS = {
    "sessionSecretKey": None,
    "sessionTimeout": 3600,
    "maxLoginAttempts": 5,
    "lockoutDuration": 300,
    "rateLimitEnabled": True,
    "rateLimitRequests": 100,
    "rateLimitWindow": 60,
}

# Smallest log file that makes sense: below this the file rotates on almost
# every line and the backups churn instead of holding history.
MIN_LOG_SIZE_BYTES = 1024
# Upper bounds, so a typo (an extra zero) cannot make a rollover walk millions
# of names, or keep backups for decades.
MAX_LOG_BACKUP_COUNT = 1000
MAX_LOG_RETENTION_DAYS = 3650

# Log defaults for an app left running for weeks-to-months: the active file is
# capped, rotated backups are gzipped, and both count and age are bounded, so
# the log folder stays in the low tens of MB no matter how chatty the host is.
# `logDir: None` means a "logs" folder in the app folder (next to the exe in a
# release, the project root from source); `logRetentionDays: 0` keeps every
# rotated backup the count cap allows.
DEFAULT_LOG_SETTINGS = {
    "logDir": None,
    "logMaxSize": "5 MB",
    "logBackupCount": 10,
    "logRotate": True,
    "logCompress": True,
    "logRetentionDays": 30,
}

_SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
}
_SIZE_PATTERN = re.compile(r"([0-9]+(?:\.[0-9]+)?)(B|KB|K|MB|M|GB|G)?")


def parse_size(value):
    """Return a size setting in bytes: plain bytes, or "5 MB" / "500KB" / "1.5 GB".

    Sizes are written the way people think about disk space, and a bare number
    keeps working, so no unit is ever guessed from context.

    Raises:
        ValueError: If the value is not a size.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # A number is already bytes, but `.inf`, 1.9 and -1 are not sizes;
        # saying so here keeps validation from failing with an OverflowError.
        if not math.isfinite(value) or value != int(value) or value < 0:
            raise ValueError(f"not a size: {value!r}")
        return int(value)

    text = str(value).strip().upper().replace("_", "").replace(" ", "")
    match = _SIZE_PATTERN.fullmatch(text)
    if not match:
        raise ValueError(f"not a size: {value!r}")
    number, unit = match.groups()
    size = float(number) * _SIZE_UNITS[unit or ""]
    if not size.is_integer():
        raise ValueError(f"not a whole number of bytes: {value!r}")
    return int(size)


class Settings:
    """Application settings held as attributes; item access mirrors attribute access."""

    def __init__(self):
        vars(self).update(
            {
                "palworldServerExePath": None,
                "palworldExeArguments": PALWORLD_EXE_ARGUMENTS,
                "palworldServerHost": "localhost",
                "palworldServerPort": 8211,
                "palworldRESTPort": 8212,
                "palworldServerAdminPassword": None,
                "useLGSM": False,
                "protocol": "REST",
                "useWebServer": True,
                "webServerPort": 8213,
                "controlServerThroughWeb": True,
                "showServerIPAddress": False,
                "autoStart": True,
                "autoStop": True,
                "autoStopDelay": 120,
                "enablePlayerTracking": True,
                "pollingRate": 5,
                "webUsername": "admin",
                "webPassword": None,
                **DEFAULT_SECURITY_SETTINGS,
                **DEFAULT_LOG_SETTINGS,
            }
        )

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def _ensure_session_secret_key(self, settings_file_path):
        """Load or generate a session secret key."""
        key_file = os.path.join(os.path.dirname(settings_file_path), "session_secret.key")
        try:
            if os.path.exists(key_file):
                with open(key_file, "r") as f:
                    saved = f.read().strip()
                if saved and len(saved) == 64:
                    self.sessionSecretKey = saved
                    return
        except OSError:
            pass

        if self.sessionSecretKey:
            return

        self.sessionSecretKey = secrets.token_hex(32)
        try:
            with open(key_file, "w") as f:
                f.write(self.sessionSecretKey)
            logging.info("Auto-generated sessionSecretKey and saved to session_secret.key")
        except OSError:
            pass

    def _load_nested_settings(self, data):
        """Load settings from nested YAML structure.

        Maps nested keys to expected flat keys for internal use.
        """
        mapping = {
            "palserver": {
                "exePath": "palworldServerExePath",
                "host": "palworldServerHost",
                "port": "palworldServerPort",
                "adminPassword": "palworldServerAdminPassword",
                "useLGSM": "useLGSM",
                "protocol": "protocol",
                "restPort": "palworldRESTPort",
                "pollingRate": "pollingRate",
                "exeArguments": "palworldExeArguments",
            },
            "web": {
                "enabled": "useWebServer",
                "port": "webServerPort",
                "username": "webUsername",
                "password": "webPassword",
                "controlServer": "controlServerThroughWeb",
                "showServerIP": "showServerIPAddress",
            },
            "features": {
                "playerTracking": "enablePlayerTracking",
                "autoStart": "autoStart",
                "autoStop": "autoStop",
            },
            "autoStop": {
                "stopDelay": "autoStopDelay",
            },
            "security": {
                "sessionSecretKey": "sessionSecretKey",
                "sessionTimeout": "sessionTimeout",
                "maxLoginAttempts": "maxLoginAttempts",
                "lockoutDuration": "lockoutDuration",
                "rateLimitEnabled": "rateLimitEnabled",
                "rateLimitRequests": "rateLimitRequests",
                "rateLimitWindow": "rateLimitWindow",
            },
            "logging": {
                "dir": "logDir",
                "maxSize": "logMaxSize",
                "backupCount": "logBackupCount",
                "rotate": "logRotate",
                "compress": "logCompress",
                "retentionDays": "logRetentionDays",
            },
        }

        flat_settings = {}

        for domain, domain_mapping in mapping.items():
            if domain in data and isinstance(data[domain], dict):
                for nested_key, flat_key in domain_mapping.items():
                    if nested_key in data[domain]:
                        flat_settings[flat_key] = data[domain][nested_key]

        for key, value in data.items():
            if key not in mapping and isinstance(value, (str, int, bool, type(None))):
                flat_settings[key] = value

        return flat_settings

    def readSettings(self, file_path):
        try:
            with open(file_path, "r") as file:
                file_data = yaml.safe_load(file)
                if file_data is None:
                    file_data = {}

                flat_settings = self._load_nested_settings(file_data)

                for key, value in flat_settings.items():
                    self[key] = value
                print("Settings loaded successfully.")
        except FileNotFoundError:
            logging.warning(f"Settings file not found: {file_path}")
        except yaml.YAMLError as e:
            logging.warning(f"Error: Invalid YAML format in {file_path}: {e}")
        except Exception:
            logging.exception("Error from readSettings")

        self._ensure_session_secret_key(file_path)

    def validate_settings(self):
        """Validate that mandatory settings are set and server executable exists.

        Raises:
            ValueError: If mandatory settings are missing or server executable doesn't exist.
        """
        errors = []

        if not self.get("palworldServerExePath"):
            errors.append("palworldServerExePath is required")

        if not self.get("palworldServerAdminPassword"):
            errors.append("palworldServerAdminPassword is required")

        if self.get("useWebServer", True) and not self.get("webPassword"):
            errors.append("webPassword is required when useWebServer is enabled")

        # Validate port ranges (1-65535) for all configured ports
        for name, value in [
            ("palworldServerPort", self.palworldServerPort),
            ("palworldRESTPort", self.palworldRESTPort),
            ("webServerPort", self.webServerPort),
        ]:
            try:
                port = int(value) if isinstance(value, str) else value
                if not 1 <= port <= 65535:
                    errors.append(f"{name} must be between 1 and 65535 (got {port})")
            except Exception:
                errors.append(f"{name} is not a valid integer: {value}")

        # Check that server executable (or, in LGSM mode, the LGSM script) exists
        server_exe_path = self.get("palworldServerExePath")
        what = "LGSM script" if self.get("useLGSM") else "Palworld server executable"
        if server_exe_path:
            resolved = shutil.which(server_exe_path) or server_exe_path
            if not os.path.exists(resolved):
                errors.append(f"{what} does not exist at: {server_exe_path}")
            elif not os.path.isfile(resolved):
                errors.append(f"{what} path is not a file: {server_exe_path}")

        self._validate_logging(errors)

        if errors:
            error_message = "Settings validation failed:\n" + "\n".join(
                f"  - {error}" for error in errors
            )
            raise ValueError(error_message)

    def _validate_logging(self, errors):
        """Collect problems with the logging settings.

        The defaults cover a normal install, so this only guards against values
        that would leave an unattended run with unbounded or missing logs.
        """
        max_size = self.get("logMaxSize")
        try:
            max_bytes = parse_size(max_size)
        except (TypeError, ValueError, OverflowError) as exc:
            errors.append(f"logMaxSize {exc} (write it as bytes, or a size like \"5 MB\")")
        else:
            if max_bytes < MIN_LOG_SIZE_BYTES:
                errors.append(
                    f"logMaxSize must be at least {MIN_LOG_SIZE_BYTES} bytes (got {max_size})"
                )

        limits = {
            "logBackupCount": MAX_LOG_BACKUP_COUNT,
            "logRetentionDays": MAX_LOG_RETENTION_DAYS,
        }
        for name, maximum in limits.items():
            value = self.get(name)
            try:
                count = int(value)
            except (TypeError, ValueError, OverflowError):
                errors.append(f"{name} is not a valid integer: {value}")
                continue
            if count < 0 or count > maximum:
                errors.append(f"{name} must be between 0 and {maximum} (got {count})")

        for name in ("logRotate", "logCompress"):
            value = self.get(name)
            if not isinstance(value, bool):
                errors.append(f"{name} must be true or false (got {value!r})")

        log_dir = self.get("logDir")
        if log_dir is not None and not isinstance(log_dir, str):
            errors.append(f"logDir must be a path (got {log_dir!r})")
        elif log_dir is not None and not log_dir.strip():
            errors.append(
                "logDir must be a path, not empty (leave it unset for the logs folder "
                "in the app folder)"
            )
        elif log_dir and os.path.exists(log_dir) and not os.path.isdir(log_dir):
            errors.append(f"logDir is not a directory: {log_dir}")

    def get_git_hash(self):
        """Return the current git commit hash, read once per instance.

        Tries to use `git rev-parse HEAD`. If git is not available or the command fails,
        returns None.
        """
        if not hasattr(self, "_git_hash"):
            self._git_hash = self._read_git_hash()
        return self._git_hash

    @staticmethod
    def _read_git_hash():
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=os.path.dirname(__file__),
                check=True,
            )
            return result.stdout.strip()
        except Exception:
            return None


settings = Settings()
