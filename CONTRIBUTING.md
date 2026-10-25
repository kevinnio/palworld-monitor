# Contributing to Palworld Monitor

## Development Setup

```bash
git clone https://github.com/kevinnio/palworld-monitor.git
cd palworld-monitor
uv sync
```

## Running Tests

**Unit tests** (no external dependencies):

```bash
uv run poe test
```

**Linting and formatting:**

```bash
uv run poe lint
uv run poe format
```

## Smoke Tests (E2E)

Smoke tests launch the full monitor app and exercise it against a dummy
PalServer (`test/e2e/dummy/PalServer-Dummy.py`). They do not require a real
Palworld server and are run in CI.

### Setup

1. Copy the example settings:

   ```bash
   cp test/e2e/settings.yaml.example test/e2e/settings.yaml
   ```

   The example uses a real PalServer executable. For a local CI-like run
   without a real server, edit `test/e2e/settings.yaml` and use the dummy:

   ```yaml
   palserver:
     exePath: "python"
     exeArguments: "test/e2e/dummy/PalServer-Dummy.py"
   ```

2. Install the Playwright browser if you have not already:

   ```bash
   uv run playwright install chromium
   ```

3. Run the smoke suite:

   ```bash
   uv run poe smoke
   ```

   To run in headed (visible browser) mode for debugging:

   ```bash
   SMOKE_HEADLESS=0 uv run poe smoke
   ```

### What the smoke tests do

- `test_autostart`: send the UDP client packet and verify the monitor spawns
  the server process.
- `test_autostop`: verify the monitor stops the server when no players are
  online.
- `test_dummy_api`: verify the dummy PalServer implements the real REST API
  contract.
- `test_web_ui`: log in, start/stop the server, and manage players through the
  browser.

## Release QA

Before preparing a release, run `uv run poe test`, `uv run poe smoke`, and
`uv run poe build-exe`. Smoke tests must use the dummy server, with no real
PalServer or unrelated service on ports 8211–8213.

Check the dashboard manually: login/logout, start/stop/restart, UDP auto-start,
empty-server auto-stop, kick, ban, and unban. Verify ban changes in two open
dashboards, after reload, and after restarting the monitor. A ban must offer
Unban even while the last player poll still says the player is online.

Check native `steam_id,player_uid` entries, preservation of unrelated entries,
cancelled actions, unavailable REST, and an unwritable disposable banlist.
Failures must not show success or erase existing bans.

Finally, use the built Windows executable against a real local PalServer with
auto-stop disabled. Use an agreed test ID, confirm Ban and Unban reach the REST
API and update its native banlist, then remove the test ban and verify all
pre-existing entries remain unchanged. Do not run smoke tests during this check.
Keep run logs and credentials in a temporary directory. Publishing still requires
separate approval after QA passes.
