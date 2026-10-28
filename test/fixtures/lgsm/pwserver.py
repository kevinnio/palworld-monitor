"""LGSM command stand-in. The serve process replaces tmux and reaps its child."""

import signal
import subprocess
import sys
import time
from pathlib import Path


def serve():
    process = None

    def shutdown(*_args):
        raise SystemExit

    signal.signal(signal.SIGTERM, shutdown)
    try:
        while True:
            if process is None and Path("start-request").exists():
                process = subprocess.Popen(
                    [sys.executable, Path("dummy-path").read_text()],
                )
                Path("server.pid").write_text(str(process.pid))
                Path("start-request").unlink()

            if (
                process is not None
                and Path("stop-request").exists()
                and Path("release-stop").exists()
            ):
                process.terminate()
                process.wait(timeout=5)
                Path("server.pid").unlink()
                Path("stop-request").unlink()
                process = None

            time.sleep(0.01)
    finally:
        if process is not None:
            process.kill()
            process.wait(timeout=5)


def main():
    command = sys.argv[1]
    if command == "serve":
        serve()
        return

    with Path("commands").open("a") as log:
        log.write(f"{command}\n")

    if command == "start" and Path("no-process").exists():
        return

    if command == "start" and Path("fail-start").exists():
        sys.exit(1)

    Path(f"{command}-request").touch()
    if Path(f"fail-{command}").exists():
        sys.exit(1)

    if Path(f"timeout-{command}").exists():
        time.sleep(30)


if __name__ == "__main__":
    main()
