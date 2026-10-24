# Palworld monitor

A Python app that manages a PalWorld dedicated server on its host: auto-starts it when a player tries to connect, auto-stops it when the server is empty, tracks players and bans, and exposes a web dashboard for control.

Explore the project before making changes. Uses `uv` for dependencies and commands — always `uv run` for Python commands; see [README.md](README.md) for user-facing setup, usage, and the full command-line flag reference.

## Commands

- `uv sync` — create `.venv` and install deps
- `uv run poe start` — start the app
- `uv run poe test` — run all tests; pass a file to run one
- `uv run poe smoke` — end-to-end tests (`test/e2e/`)
- `uv run poe lint` — ruff check
- `uv run poe format` — ruff format
- `uv run poe build-exe` — build a Windows exe into `dist/`

## Architecture

- `src/main.py` — thin entrypoint and composition root. Wires CLI (`src/cli.py`), the first-run setup wizard (`src/setup_wizard.py`), an instance-local `EventBus`, the player/banlist managers, the OS process backend, the controller, auto-start and the web server. It also calls `start()`/`close()` on those objects.
- `PalWorldController` (`src/palworld_control.py`) — single-writer lifecycle supervisor. It owns the explicit `STOPPED`/`STARTING`/`RUNNING`/`STOPPING` state, the current server generation, a monotonic snapshot revision, and the process identity (PID plus creation time when available). Worker threads do blocking I/O (process detection, REST polling, launch, termination) and submit observations tagged with the generation/sequence they started under; the supervisor is the only writer of lifecycle, roster and auto-stop state, and publishes a notification only after committing it. Stale or delayed observations are discarded. Startup reconciles stale PID files and persisted online players with `STOPPED`; a tracked PID is adopted only when the current scan also finds it as PalServer, and a start is guarded against an undiscovered found process. A failed stop returns to an observed `RUNNING` state and resumes polling, while a pending LGSM shutdown stays `STOPPING` until the exit is observed. Auto-stop requires a fresh empty roster sample and revalidates when the latest sample is stale, unavailable, due or in flight.
- `src/events.py` — instance-local `EventBus` (`subscribe`, `unsubscribe`, `publish`). Consumers receive committed notifications (`SERVER_STARTED`, `SERVER_STOPPED`, `SERVER_STATUS`, `LIFECYCLE_CHANGED`) and read the controller snapshot; they never drive lifecycle state themselves.
- `AutoStartManager` (`src/auto_start.py`) — listens on the Palworld game port when the committed state is `STOPPED`, releases the port when a start begins, and starts the server when a client connection attempt is detected.
- Process control (`src/process_manager.py`) — passive backends that spawn/stop/kill PalServer per OS, with a LinuxGSM variant for Linux hosts managed by LinuxGSM. They track the expected process identity with compare-and-clear, never publish lifecycle events, and report exits as observations. `terminate_process()` returns `True` when gone, `False` when the process is still alive, and `None` while an LGSM shutdown watchdog is still watching.
- `RestClient` (`src/api_clients.py`) — talks to the PalServer REST API for status, players, kick/ban, etc. `get_player_names()` returns `None` when REST is unavailable, distinct from a successful empty roster.
- `WebServer` (`src/web_server.py`) — Flask dashboard. Initial page loads and SSE connections read one committed snapshot from the controller (`revision`, `state`, `playerData`, `playerCount`, `players`, `banned_players`), and every committed transition refreshes and broadcasts that snapshot under one lock. Revisions order SSE updates; the UI ignores older snapshots.
- Configuration lives in `src/settings.yaml` (gitignored, copy from `src/settings.yaml.example`); CLI flags override it.

## Testing

- Unit tests in `test/`, fixtures in `test/conftest.py`.
- Lifecycle tests use inactive controllers with fake process managers/clients, a fake monotonic clock and an injected `EventBus`; `PlayerManager`/`BanlistManager` write to `tmp_path`. They call `start()`/`close()` explicitly and do not clear global bus subscribers.
- E2E tests (`test/e2e/`) use a dummy PalServer (`test/e2e/dummy/PalServer-Dummy.py`) — no real server needed, but they are destructive: they kill any PalServer process and free ports 8211–8213. Do not run them on a machine running a real PalServer.
- Manual QA scenarios and live-server rules live in `.agents/skills/palworld-qa/`.

## Constraints

- Must run on the same host as the Palworld server (needs process spawn/kill access).
- Only `pytest` for CI. No linter/typecheck step.

## Commits

- Titles short and to the point; descriptions high-level, in natural language.

## References

- [Palworld REST API docs](https://docs.palworldgame.com/category/rest-api) — consult when touching `src/api_clients.py` or the dummy server.
