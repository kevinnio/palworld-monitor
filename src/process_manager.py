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

import logging
import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import psutil

from src.settings import settings


@dataclass(frozen=True)
class ProcessIdentity:
    """A process we own, identified by PID and (when available) creation time."""

    pid: int
    create_time: Optional[float] = None

    def matches(self, other: Optional["ProcessIdentity"]) -> bool:
        if other is None or self.pid != other.pid:
            return False
        if self.create_time is not None and other.create_time is not None:
            return abs(self.create_time - other.create_time) < 0.01
        return True


@dataclass(frozen=True)
class ProcessSnapshot:
    """One I/O observation of the tracked and any discovered PalServer process."""

    tracked: Optional[ProcessIdentity]
    tracked_running: bool
    found: Optional[ProcessIdentity]


class OSProcessManager:
    def __init__(self, on_exit: Optional[Callable[[ProcessIdentity], None]] = None):
        self._lock = threading.Lock()
        self._identity: Optional[ProcessIdentity] = None
        self._on_exit = on_exit
        self._closing = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._load_identity_from_file()

    def pid_file_name(self):
        raise NotImplementedError

    def set_exit_callback(self, callback: Callable[[ProcessIdentity], None]):
        self._on_exit = callback

    @property
    def tracked_identity(self) -> Optional[ProcessIdentity]:
        with self._lock:
            return self._identity

    def _identity_for_pid(self, pid) -> ProcessIdentity:
        pid = int(pid)
        try:
            return ProcessIdentity(pid, psutil.Process(pid).create_time())
        except Exception:
            return ProcessIdentity(pid, None)

    def _save_pid_to_file(self, pid):
        try:
            with open(self.pid_file_name(), "w") as f:
                f.write(str(pid))
        except Exception:
            pass

    def _load_identity_from_file(self):
        if not os.path.exists(self.pid_file_name()):
            return
        try:
            with open(self.pid_file_name(), "r") as f:
                pid = int(f.read().strip())
        except Exception:
            return
        self._identity = self._identity_for_pid(pid)

    def _remove_pid_file(self):
        if os.path.exists(self.pid_file_name()):
            try:
                os.remove(self.pid_file_name())
            except Exception:
                pass

    def set_known_identity(self, identity: ProcessIdentity):
        """Record the process we own before any SERVER_STARTED is committed."""
        with self._lock:
            self._identity = identity
            self._save_pid_to_file(identity.pid)

    def clear_identity(self, expected: Optional[ProcessIdentity]) -> bool:
        """Compare-and-clear: never forget a replacement process."""
        with self._lock:
            current = self._identity
            if current is None or expected is None or not current.matches(expected):
                return False
            self._identity = None
            self._remove_pid_file()
            return True

    def identity_is_running(self, identity: Optional[ProcessIdentity]) -> bool:
        if identity is None:
            return False
        try:
            process = psutil.Process(identity.pid)
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return psutil.pid_exists(identity.pid)
        except Exception:
            return False

        try:
            if identity.create_time is not None:
                try:
                    if abs(process.create_time() - identity.create_time) > 0.01:
                        return False
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if process.is_running():
                return True
            return len(process.children(recursive=True)) > 0
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return False
        except psutil.AccessDenied:
            return True
        except Exception:
            return False

    def find_process_identity(self, name) -> Optional[ProcessIdentity]:
        try:
            target = (name or "").lower()
            if not target:
                return None
            for proc in psutil.process_iter(
                attrs=["pid", "name", "exe", "cmdline", "create_time"]
            ):
                try:
                    info = proc.info
                    exe = (info.get("exe") or "").lower()
                    pname = (info.get("name") or "").lower()
                    cmdline = info.get("cmdline") or []
                    if target in exe or target in pname:
                        return ProcessIdentity(info["pid"], info.get("create_time"))
                    # Check script basename for Python processes running a .py file
                    if "python" not in pname:
                        continue
                    if cmdline and len(cmdline) > 1:
                        script = cmdline[-1]
                        # Skip -c scripts (contain newlines)
                        if (
                            "\n" not in script
                            and target in os.path.basename(script).lower()
                        ):
                            return ProcessIdentity(info["pid"], info.get("create_time"))
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    continue
        except Exception:
            pass
        return None

    def observe(self) -> ProcessSnapshot:
        """I/O-only observation for the lifecycle supervisor."""
        tracked = self.tracked_identity
        return ProcessSnapshot(
            tracked=tracked,
            tracked_running=self.identity_is_running(tracked),
            found=self.find_process_identity("PalServer"),
        )

    def launch_process(self, _exe_path, _exe_args) -> Optional[ProcessIdentity]:
        raise NotImplementedError

    def terminate_process(self, expected: Optional[ProcessIdentity] = None) -> Optional[bool]:
        """Terminate the tracked process, or confirm it is already gone.

        Returns True when the expected process is gone, False when it is still
        running after a failed attempt, and None while an asynchronous shutdown
        watchdog is still watching it.
        """
        with self._lock:
            current = self._identity
        if current is None:
            return False
        if expected is not None and not current.matches(expected):
            return False

        try:
            process = psutil.Process(current.pid)
            if current.create_time is not None:
                try:
                    if abs(process.create_time() - current.create_time) > 0.01:
                        # The PID now belongs to a replacement; leave it alone.
                        return False
                except psutil.Error:
                    pass
            children = process.children(recursive=True)
            for child in children:
                child.terminate()
            process.terminate()
            try:
                process.wait(timeout=30)
            except psutil.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    return False
            self.clear_identity(current)
            return True
        except psutil.NoSuchProcess:
            self.clear_identity(current)
            return True
        except psutil.AccessDenied:
            return False
        except Exception:
            return False

    def close(self):
        self._closing.set()
        watchdog = self._watchdog_thread
        if (
            watchdog is not None
            and watchdog.is_alive()
            and watchdog is not threading.current_thread()
        ):
            watchdog.join(timeout=5)

    def _notify_exit(self, identity: ProcessIdentity):
        if self._on_exit is not None:
            try:
                self._on_exit(identity)
            except Exception:
                logging.exception("Process exit callback failed")


class WindowsProcessManager(OSProcessManager):
    def pid_file_name(self):
        return "palworld_server.win.pid"

    def launch_process(self, exe_path, exe_args) -> Optional[ProcessIdentity]:
        process = subprocess.Popen(
            [exe_path] + exe_args.split(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=(
                subprocess.HIGH_PRIORITY_CLASS
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            ),
        )
        identity = self._identity_for_pid(process.pid)
        self.set_known_identity(identity)
        return identity


class LinuxProcessManager(OSProcessManager):
    def pid_file_name(self):
        return "palworld_server.linux.pid"

    def launch_process(self, exe_path, exe_args) -> Optional[ProcessIdentity]:
        process = subprocess.Popen(
            [exe_path] + exe_args.split(),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        identity = self._identity_for_pid(process.pid)
        self.set_known_identity(identity)
        return identity


class LGSMProcessManager(OSProcessManager):
    """Process manager for a PalServer managed by LinuxGSM (LGSM).

    `exe_path`/`palworldServerExePath` is the LGSM instance script (e.g.
    `/home/gameserver/pwserver/pwserver`), not the PalServer binary itself.
    Start/stop go through the script's own `start`/`stop` commands rather
    than spawning or killing the game process directly, because LGSM tracks
    its own idea of "running" (lock files, tmux/screen session) and its
    `monitor` cron job will restart the server if it looks like it crashed —
    killing the process out from under LGSM would fight that.
    """

    STARTUP_TIMEOUT = 30
    STARTUP_POLL_INTERVAL = 1
    SHUTDOWN_VERIFY_TIMEOUT = 30
    SHUTDOWN_VERIFY_POLL_INTERVAL = 1
    SHUTDOWN_WATCHDOG_POLL_INTERVAL = 3
    LGSM_COMMAND_TIMEOUT = 120

    def __init__(self, on_exit=None):
        self._exe_path = None
        super().__init__(on_exit=on_exit)

    def pid_file_name(self):
        return "palworld_server.lgsm.pid"

    def _run_lgsm_command(self, exe_path, command):
        try:
            result = subprocess.run(
                [exe_path, command],
                cwd=os.path.dirname(exe_path) or ".",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.LGSM_COMMAND_TIMEOUT,
            )
            if result.returncode != 0:
                logging.warning(
                    f"LGSM '{command}' command on {exe_path} exited with code "
                    f"{result.returncode}: {result.stderr.strip()}"
                )
            return True
        except Exception as e:
            logging.error(f"Error running LGSM '{command}' command on {exe_path}: {e}")
            return False

    def launch_process(self, exe_path, _exe_args) -> Optional[ProcessIdentity]:
        self._exe_path = exe_path
        if not self._run_lgsm_command(exe_path, "start"):
            return None

        deadline = time.monotonic() + self.STARTUP_TIMEOUT
        identity = None
        while time.monotonic() < deadline:
            identity = self.find_process_identity("PalServer")
            if identity:
                break
            time.sleep(self.STARTUP_POLL_INTERVAL)

        if not identity:
            logging.error(
                "LGSM 'start' completed but no PalServer process was found "
                f"within {self.STARTUP_TIMEOUT}s. Check the LGSM script's own logs."
            )
            return None

        self.set_known_identity(identity)
        return identity

    def terminate_process(self, expected: Optional[ProcessIdentity] = None) -> Optional[bool]:
        with self._lock:
            current = self._identity
            exe_path = self._exe_path or settings.palworldServerExePath
        if current is None:
            return False
        if expected is not None and not current.matches(expected):
            return False

        if not exe_path:
            logging.error("Cannot stop LGSM server: no LGSM script path is known.")
            return False

        # Kick off the stop command regardless of whether it reports success —
        # a failed/timed-out command invocation doesn't mean the server isn't
        # genuinely shutting down (Palworld's save-on-shutdown can run long).
        command_ok = self._run_lgsm_command(exe_path, "stop")

        if self._wait_for_identity_gone(
            current, self.SHUTDOWN_VERIFY_TIMEOUT, self.SHUTDOWN_VERIFY_POLL_INTERVAL
        ):
            self.clear_identity(current)
            return True

        # Don't give up here: if we did, the supervisor would stay in STOPPING
        # until the next detection poll. Keep watching in the background and
        # report the exit as an observation.
        logging.warning(
            f"LGSM 'stop' hasn't confirmed PID {current.pid} is gone after "
            f"{self.SHUTDOWN_VERIFY_TIMEOUT}s (command "
            f"{'completed' if command_ok else 'failed/timed out'}); "
            "continuing to watch for it in the background."
        )
        watchdog = threading.Thread(
            target=self._watch_for_termination,
            args=(current,),
            name="palworld-lgsm-watchdog",
            daemon=True,
        )
        self._watchdog_thread = watchdog
        watchdog.start()
        return None

    def _wait_for_identity_gone(
        self, identity: ProcessIdentity, timeout: float, interval: float
    ) -> bool:
        deadline = time.monotonic() + timeout
        while self.identity_is_running(identity):
            if self._closing.is_set() or time.monotonic() >= deadline:
                return False
            time.sleep(interval)
        return True

    def _watch_for_termination(self, identity: ProcessIdentity):
        while self.identity_is_running(identity):
            if self._closing.wait(self.SHUTDOWN_WATCHDOG_POLL_INTERVAL):
                return
        logging.info(
            f"LGSM background watchdog confirmed PID {identity.pid} has exited."
        )
        if self.clear_identity(identity):
            self._notify_exit(identity)


def create_process_manager() -> OSProcessManager:
    """Composition helper: pick the backend for this host."""
    if platform.system().lower() == "linux":
        if getattr(settings, "useLGSM", False):
            return LGSMProcessManager()
        return LinuxProcessManager()
    return WindowsProcessManager()
