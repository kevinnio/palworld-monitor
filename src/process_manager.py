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

import subprocess
import psutil
import os
import threading
import time
import logging
from src.events import bus, Event
from src.settings import settings


class OSProcessManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.launched_pid = None
        self._load_pid_from_file()
        bus.subscribe(
            Event.CMD_START_SERVER,
            lambda data: self.launch_process(data["exe_path"], data["exe_args"]),
        )
        bus.subscribe(Event.CMD_STOP_SERVER, lambda data: self.terminate_process())

    def pid_file_name(self):
        raise NotImplementedError

    def _after_launch(self, process):
        self._after_launch_pid(process.pid)

    def _after_launch_pid(self, pid):
        with self._lock:
            self.launched_pid = pid
            self._save_pid_to_file(pid)

    def _save_pid_to_file(self, pid):
        try:
            with open(self.pid_file_name(), "w") as f:
                f.write(str(pid))
        except Exception:
            pass

    def _load_pid_from_file(self):
        if os.path.exists(self.pid_file_name()):
            try:
                with open(self.pid_file_name(), "r") as f:
                    pid = int(f.read().strip())
                    self.launched_pid = pid
            except Exception:
                self.launched_pid = None

    def _remove_pid_file(self):
        if os.path.exists(self.pid_file_name()):
            try:
                os.remove(self.pid_file_name())
            except Exception:
                pass

    def set_known_pid(self, pid):
        with self._lock:
            try:
                self.launched_pid = int(pid)
                self._save_pid_to_file(self.launched_pid)
            except Exception:
                self.launched_pid = None

    def clear_pid(self):
        """Forget the tracked process (it exited outside our control)."""
        with self._lock:
            self.launched_pid = None
            self._remove_pid_file()

    def launch_process(self, _exe_path, _exe_args):
        raise NotImplementedError

    def is_process_running(self):
        with self._lock:
            pid = self.launched_pid
        if pid is None:
            return False
        try:
            process = psutil.Process(pid)
            if process.is_running():
                return True
            children = process.children(recursive=True)
            if len(children) > 0:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception:
            return False

        # The pid we launched is gone. The server may have been restarted
        # outside of our control (e.g. a Steam/LGSM update cron restarting
        # it) with a new pid -- try to re-acquire it before reporting the
        # server as offline, since REST-based player tracking would keep
        # working fine the whole time and make "offline" look wrong.
        new_pid = self.find_process_pid("PalServer")
        if new_pid:
            logging.info(
                f"Tracked PID {pid} is gone; re-acquired PalServer as PID "
                f"{new_pid} (likely restarted outside palworld-monitor)."
            )
            self.set_known_pid(new_pid)
            return True
        return False

    def terminate_process(self):
        with self._lock:
            if self.launched_pid is None:
                return False
            try:
                process = psutil.Process(self.launched_pid)
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
                        # ponytail: process is unkillable, don't lie —
                        # return False so callers know it's still alive.
                        return False
                self._remove_pid_file()
                terminated_pid = self.launched_pid
                self.launched_pid = None
                bus.publish(Event.SERVER_STOPPED, {"pid": terminated_pid})
                return True
            except psutil.NoSuchProcess:
                self._remove_pid_file()
                self.launched_pid = None
                return False
            except psutil.AccessDenied:
                return False
            except Exception:
                return False

    def find_process_pid(self, name):
        try:
            target = (name or "").lower()
            if not target:
                return None
            for proc in psutil.process_iter(attrs=["pid", "name", "exe", "cmdline"]):
                try:
                    info = proc.info
                    exe = (info.get("exe") or "").lower()
                    pname = (info.get("name") or "").lower()
                    cmdline = info.get("cmdline") or []
                    if target in exe or target in pname:
                        return info["pid"]
                    # Check script basename for Python processes running a .py file
                    if "python" not in pname:
                        continue
                    if cmdline and len(cmdline) > 1:
                        script = cmdline[-1]
                        # Skip -c scripts (contain newlines)
                        if "\n" not in script and target in os.path.basename(script).lower():
                            return info["pid"]
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    continue
        except Exception:
            pass
        return None

class WindowsProcessManager(OSProcessManager):
    def pid_file_name(self):
        return "palworld_server.win.pid"

    def launch_process(self, exe_path, exe_args):
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
        self._after_launch(process)
        bus.publish(Event.SERVER_STARTED, {"pid": self.launched_pid})


class LinuxProcessManager(OSProcessManager):
    def pid_file_name(self):
        return "palworld_server.linux.pid"

    def launch_process(self, exe_path, exe_args):
        process = subprocess.Popen(
            [exe_path] + exe_args.split(),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        self._after_launch(process)
        bus.publish(Event.SERVER_STARTED, {"pid": self.launched_pid})


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

    def __init__(self):
        self._exe_path = None
        super().__init__()

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

    def launch_process(self, exe_path, _exe_args):
        self._exe_path = exe_path
        if not self._run_lgsm_command(exe_path, "start"):
            return

        deadline = time.time() + self.STARTUP_TIMEOUT
        pid = None
        while time.time() < deadline:
            pid = self.find_process_pid("PalServer")
            if pid:
                break
            time.sleep(self.STARTUP_POLL_INTERVAL)

        if not pid:
            logging.error(
                "LGSM 'start' completed but no PalServer process was found "
                f"within {self.STARTUP_TIMEOUT}s. Check the LGSM script's own logs."
            )
            return

        self._after_launch_pid(pid)
        bus.publish(Event.SERVER_STARTED, {"pid": self.launched_pid})

    def terminate_process(self):
        with self._lock:
            if self.launched_pid is None:
                return False
            pid = self.launched_pid
            exe_path = self._exe_path or settings.palworldServerExePath

        if not exe_path:
            logging.error("Cannot stop LGSM server: no LGSM script path is known.")
            return False

        # Kick off the stop command regardless of whether it reports success —
        # a failed/timed-out command invocation doesn't mean the server isn't
        # genuinely shutting down (Palworld's save-on-shutdown can run long).
        command_ok = self._run_lgsm_command(exe_path, "stop")

        if self._wait_for_pid_gone(
            pid, self.SHUTDOWN_VERIFY_TIMEOUT, self.SHUTDOWN_VERIFY_POLL_INTERVAL
        ):
            self._finish_termination(pid)
            return True

        # Don't give up here: if we did, no SERVER_STOPPED event ever fires,
        # which permanently breaks the auto-start listener (it only re-arms
        # on that event) even after the server actually finishes stopping.
        # Keep watching in the background for as long as it takes.
        logging.warning(
            f"LGSM 'stop' hasn't confirmed PID {pid} is gone after "
            f"{self.SHUTDOWN_VERIFY_TIMEOUT}s (command "
            f"{'completed' if command_ok else 'failed/timed out'}); "
            "continuing to watch for it in the background."
        )
        threading.Thread(
            target=self._watch_for_termination, args=(pid,), daemon=True
        ).start()
        return False

    def _wait_for_pid_gone(self, pid, timeout, interval):
        deadline = time.time() + timeout
        while psutil.pid_exists(pid):
            if time.time() >= deadline:
                return False
            time.sleep(interval)
        return True

    def _watch_for_termination(self, pid):
        while psutil.pid_exists(pid):
            time.sleep(self.SHUTDOWN_WATCHDOG_POLL_INTERVAL)
        logging.info(f"LGSM background watchdog confirmed PID {pid} has exited.")
        self._finish_termination(pid)

    def _finish_termination(self, pid):
        with self._lock:
            if self.launched_pid != pid:
                return  # already handled (e.g. by a concurrent watchdog)
            self._remove_pid_file()
            self.launched_pid = None
        bus.publish(Event.SERVER_STOPPED, {"pid": pid})
