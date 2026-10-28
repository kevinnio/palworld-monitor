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
- `PalWorldController` (`src/palworld_control.py`) — small application facade: start/close, start/stop server, state/snapshot access, kick/ban/unban and ban queries. It delegates lifecycle work to the supervisor and runtime.
- Lifecycle models (`src/lifecycle/messages.py`) — `LifecycleState`, process identity/snapshot models, request/result/observation dataclasses and command types. Data only; no application imports.
- Lifecycle supervisor (`src/lifecycle/supervisor.py`) — the only writer of lifecycle, roster and auto-stop state. It owns the explicit `STOPPED`/`STARTING`/`RUNNING`/`STOPPING` state, the current server generation, a monotonic snapshot revision, and the process identity (PID plus creation time when available). `handle()`/`on_tick()` are synchronous and return outbound work requests; they never manage threads or queues. Stale or delayed observations are discarded. Startup reconciles stale PID files and persisted online players with `STOPPED`; a tracked PID is adopted only when the current scan also finds it as PalServer, and a start is guarded against an undiscovered found process. A failed stop returns to an observed `RUNNING` state and resumes polling, while a pending LGSM shutdown stays `STOPPING` until the exit is observed.
- Auto-stop policy (`src/lifecycle/auto_stop.py`) — pure deadline/freshness/revalidation decisions over a small `AutoStopState` value object. No I/O, bus, process manager, PlayerManager or controller access. The supervisor applies the returned state and decision; expiry requires a fresh empty sample.
- Lifecycle runtime (`src/lifecycle/runtime.py`) — owns four threads: lifecycle, process detection, REST polling, and one shared launch/stop worker. Process changes are serialized, while detection and commands remain responsive during blocked REST or process I/O. Only the lifecycle thread calls the supervisor, and the runtime never mutates lifecycle state directly.
- `src/events.py` — instance-local `EventBus` (`subscribe`, `unsubscribe`, `publish`). Consumers receive committed notifications (`SERVER_STARTED`, `SERVER_STOPPED`, `SERVER_STATUS`, `LIFECYCLE_CHANGED`) and read the controller snapshot; they never drive lifecycle state themselves.
- `AutoStartManager` (`src/auto_start.py`) — listens on the Palworld game port when the committed state is `STOPPED`, releases the port when a start begins, and starts the server when a client connection attempt is detected.
- Process control (`src/process_manager.py`) — passive backends that spawn/stop/kill PalServer per OS, with a LinuxGSM variant for Linux hosts managed by LinuxGSM. They share the process models from `src/lifecycle/messages.py`, track the expected process identity with compare-and-clear, never publish lifecycle events, and own no threads. `terminate_process()` returns `True` when gone, `False` when the process is still alive, and `None` while an LGSM shutdown is pending. The runtime's detection worker observes delayed exits at the configured polling rate.
- `RestClient` (`src/api_clients.py`) — talks to the PalServer REST API for status, players, kick/ban, etc. `get_player_names()` returns `None` when REST is unavailable, distinct from a successful empty roster.
- `WebServer` (`src/web_server.py`) — Flask dashboard. Initial page loads and SSE connections read one committed snapshot from the controller (`revision`, `state`, `playerData`, `playerCount`, `players`, `banned_players`), and every committed transition refreshes and broadcasts that snapshot under one lock. Revisions order SSE updates; the UI ignores older snapshots.
- Configuration lives in `src/settings.yaml` (gitignored, copy from `src/settings.yaml.example`); CLI flags override it.

## Testing

- Unit tests in `test/`, integration tests in `test/integration/`, shared fixtures in `test/conftest.py`. `uv run poe test` runs both suites.
- Integration coverage includes native process backends, LGSM, controller/runtime wiring, Flask request flows, and the dummy REST API contract. Browser tests remain in `test/e2e/`.
- Lifecycle tests use inactive controllers with fake process managers/clients, a fake monotonic clock and an injected `EventBus`; `PlayerManager`/`BanlistManager` write to `tmp_path`. They call `start()`/`close()` explicitly and do not clear global bus subscribers.
- Boundary tests: `test/test_lifecycle_supervisor.py` covers synchronous transitions/adoption/stale observations, `test/test_lifecycle_auto_stop.py` covers the pure policy, and `test/integration/test_lifecycle_runtime.py` covers blocked I/O and cleanup. `test/integration/test_palworld_control.py` keeps facade/integration coverage. `test/integration/test_process_manager.py` shares native Windows/Linux process contracts. `test/integration/test_lgsm.py` runs a stand-in LGSM script and the dummy server on Linux.
- E2E tests (`test/e2e/`) use a dummy PalServer (`test/e2e/dummy/PalServer-Dummy.py`) — no real server needed, but they are destructive: they kill any PalServer process and free ports 8211–8213. Do not run them on a machine running a real PalServer.
- Manual QA scenarios and live-server rules live in `.agents/skills/palworld-qa/`.

## Constraints

- Must run on the same host as the Palworld server (needs process spawn/kill access).
- Only `pytest` for CI. No linter/typecheck step.

## Commits

- Titles short and to the point; descriptions high-level, in natural language.

## References

- [Palworld REST API docs](https://docs.palworldgame.com/category/rest-api) — consult when touching `src/api_clients.py` or the dummy server.
