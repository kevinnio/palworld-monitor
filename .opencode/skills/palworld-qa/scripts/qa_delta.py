#!/usr/bin/env python3
"""Show code changes since the last Palworld QA verification.

Run from the project root. With --mark-verified, record the current HEAD and
working-tree status so the next run only reports newer changes.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STATE = SKILL_DIR / "references" / "qa-state.json"

AREA_PATTERNS = (
    (
        "lifecycle/process",
        (
            "src/palworld_control.py",
            "src/lifecycle/",
            "src/events.py",
            "src/process_manager.py",
            "src/auto_start.py",
        ),
    ),
    ("web-ui", ("src/web_server.py", "src/templates/", "src/static/")),
    (
        "players-bans",
        ("src/player_manager.py", "src/banlist_manager.py", "src/api_clients.py"),
    ),
    (
        "settings-setup",
        ("src/settings.py", "src/cli.py", "src/setup_wizard.py", "src/main.py"),
    ),
    ("tests", ("test/",)),
    ("docs", ("AGENTS.md", "README.md", "docs/")),
)


def git(root: Path, *args: str, strip: bool = True):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() if strip else result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"error: git {' '.join(args)} failed: {exc}", file=sys.stderr)
        return None


def read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def classify(files) -> dict:
    groups = {}
    for path in sorted(files):
        area = "other"
        for name, prefixes in AREA_PATTERNS:
            if any(path == prefix or path.startswith(prefix) for prefix in prefixes):
                area = name
                break
        groups.setdefault(area, []).append(path)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=Path.cwd(), type=Path)
    parser.add_argument("--state", default=DEFAULT_STATE, type=Path)
    parser.add_argument("--since", help="Override the last verified commit")
    parser.add_argument("--mark-verified", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    state_path = args.state
    state = read_state(state_path)
    head = git(root, "rev-parse", "HEAD")
    since = args.since or state.get("last_verified_commit")
    files = set()

    if since:
        changed = git(root, "diff", "--name-only", f"{since}..HEAD")
        if changed:
            files.update(changed.splitlines())
            print(f"Committed changes since {since[:12]}:")
            for path in changed.splitlines():
                print(f"  {path}")
        else:
            print(f"No committed changes since {since[:12]}.")
    else:
        print("No previous QA state; inspect the full catalog and establish a baseline.")

    status = git(root, "status", "--porcelain", strip=False)
    if status and status != state.get("last_status"):
        print("\nUncommitted/untracked changes:")
        for line in status.splitlines():
            print(f"  {line}")
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.add(path)
    elif status:
        print("\nWorking tree unchanged since the last QA verification.")

    if files:
        print("\nChanged areas:")
        for area, paths in classify(files).items():
            print(f"  {area}:")
            for path in paths:
                print(f"    {path}")
        print("\nUpdate references/qa-checklist.md for these areas before testing.")
    else:
        print("\nNo changed files detected since the last verified commit.")

    if args.mark_verified:
        if not head:
            print("error: cannot mark verified without a git HEAD", file=sys.stderr)
            return 1
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "last_verified_commit": head,
                    "last_status": status or "",
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nMarked {head[:12]} verified in {state_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
