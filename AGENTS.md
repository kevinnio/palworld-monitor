# Palworld monitor

A Python app that manages a PalWorld dedicated server on its host: auto-starts it when a player tries to connect, auto-stops it when the server is empty, tracks players and bans, and exposes a web dashboard for control.

Explore the project before making changes. Uses `uv` for dependencies and commands; see [README.md](README.md) for user-facing setup, usage, and the full command-line flag reference.

## Commands

- `uv sync` — create `.venv` and install deps
- `uv run poe start` — start the app
- `uv run poe test` — run all tests; pass a file to run one
- `uv run poe smoke` — end-to-end tests (`test/e2e/`)
- `uv run poe lint` — ruff check
- `uv run poe format` — ruff format
- `uv run poe build-exe` — build a Windows exe into `dist/`

## Architecture

- `src/main.py` — thin entrypoint; wires up CLI (`src/cli.py`) and the first-run setup wizard (`src/setup_wizard.py`).
- `PalWorldController` (`src/palworld_control.py`) — the orchestrator. Coordinates the auto-start listener, the process manager, and the web server via an in-process event bus (`src/events.py`).
- `AutoStartManager` (`src/auto_start.py`) — listens on the Palworld game port and starts the server when a client connection attempt is detected.
- Process control (`src/process_manager.py`) — spawns/stops/kills PalServer per OS, with a LinuxGSM variant for Linux hosts managed by LinuxGSM.
- `RestClient` (`src/api_clients.py`) — talks to the PalServer REST API for status, players, kick/ban, etc.
- `WebServer` (`src/web_server.py`) — Flask dashboard, pushes live updates over Server-Sent Events.
- Configuration lives in `src/settings.yaml` (gitignored, copy from `src/settings.yaml.example`); CLI flags override it.

## Testing

- Unit tests in `test/`, fixtures in `test/conftest.py`.
- E2E tests (`test/e2e/`) use a dummy PalServer (`test/e2e/dummy/PalServer-Dummy.py`) — no real server needed, but they are destructive: they kill any PalServer process and free ports 8211–8213. Do not run them on a machine running a real PalServer.

## Constraints

- Must run on the same host as the Palworld server (needs process spawn/kill access).
- Only `pytest` for CI. No linter/typecheck step.

## References

- [Palworld REST API docs](https://docs.palworldgame.com/category/rest-api) — consult when touching `src/api_clients.py` or the dummy server.
