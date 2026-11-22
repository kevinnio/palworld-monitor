"""Monitor version for source checkouts and packaged releases."""

import subprocess
import sys
from functools import cache
from pathlib import Path

VERSION_FILE = "monitor-version.txt"


def resolve_git_version(root: Path) -> str | None:
    """Use a release tag on HEAD, never a tag from an earlier commit."""
    for args in (
        ["describe", "--tags", "--exact-match", "--match", "v[0-9]*", "HEAD"],
        ["rev-parse", "--short=7", "HEAD"],
    ):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if version := result.stdout.strip():
            return version
    return None


@cache
def get_monitor_version() -> str | None:
    if getattr(sys, "frozen", False):
        try:
            return (Path(sys._MEIPASS) / VERSION_FILE).read_text(
                encoding="utf-8"
            ).strip() or None
        except OSError:
            return None
    return resolve_git_version(Path(__file__).resolve().parents[1])
