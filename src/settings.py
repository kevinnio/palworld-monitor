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
import traceback
import os
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


class Settings:
    def __init__(self):
        self.settings = {
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
        }
        for key, value in self.settings.items():
            setattr(self, key, value)

    def __getitem__(self, key):
        return self.settings[key]

    def __setitem__(self, key, value):
        self.settings[key] = value
        setattr(self, key, value)

    def _ensure_session_secret_key(self, settings_file_path):
        """Load or generate a session secret key."""
        key_file = os.path.join(os.path.dirname(settings_file_path), "session_secret.key")
        try:
            if os.path.exists(key_file):
                with open(key_file, "r") as f:
                    saved = f.read().strip()
                if saved and len(saved) == 64:
                    self.settings["sessionSecretKey"] = saved
                    self.sessionSecretKey = saved
                    return
        except OSError:
            pass

        if self.settings.get("sessionSecretKey"):
            return

        self.settings["sessionSecretKey"] = secrets.token_hex(32)
        self.sessionSecretKey = self.settings["sessionSecretKey"]
        try:
            with open(key_file, "w") as f:
                f.write(self.settings["sessionSecretKey"])
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
                    self.settings[key] = value
                    setattr(self, key, value)
                print("Settings loaded successfully.")
        except FileNotFoundError:
            logging.warning(f"Settings file not found: {file_path}")
        except yaml.YAMLError as e:
            logging.warning(f"Error: Invalid YAML format in {file_path}: {e}")
        except Exception as e:
            logging.error(f"Error from readSettings: {e}")
            logging.error(traceback.format_exc())

        self._ensure_session_secret_key(file_path)

    def validate_settings(self):
        """Validate that mandatory settings are set and server executable exists.

        Raises:
            ValueError: If mandatory settings are missing or server executable doesn't exist.
        """
        errors = []

        if not self.settings.get("palworldServerExePath"):
            errors.append("palworldServerExePath is required")

        if not self.settings.get("palworldServerAdminPassword"):
            errors.append("palworldServerAdminPassword is required")

        if self.settings.get("useWebServer", True) and not self.settings.get("webPassword"):
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
        server_exe_path = self.settings.get("palworldServerExePath")
        what = "LGSM script" if self.settings.get("useLGSM") else "Palworld server executable"
        if server_exe_path:
            resolved = shutil.which(server_exe_path) or server_exe_path
            if not os.path.exists(resolved):
                errors.append(f"{what} does not exist at: {server_exe_path}")
            elif not os.path.isfile(resolved):
                errors.append(f"{what} path is not a file: {server_exe_path}")

        if errors:
            error_message = "Settings validation failed:\n" + "\n".join(
                f"  - {error}" for error in errors
            )
            raise ValueError(error_message)

    def get_git_hash(self):
        """Return the current git commit hash.
        Tries to use `git rev-parse HEAD`. If git is not available or the command fails,
        returns None.
        """
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
