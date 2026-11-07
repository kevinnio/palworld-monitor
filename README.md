# PalWorld Monitor

![CI](https://github.com/kevinnio/palworld-monitor/actions/workflows/ci.yml/badge.svg)

Manages a PalWorld dedicated server on Windows or Linux: starts it when a player
connects, stops it when the server is empty, and provides a web dashboard for
monitoring and control. It must run on the same host as the server (your PC,
the same VM, the same Docker container, etc.) so it can launch and signal the
game process.

## Setup

1. Download from the latest [GitHub Release](https://github.com/kevinnio/palworld-monitor/releases):
   - **Windows:** `palworld-monitor-windows.exe`
   - **Linux:** `palworld-monitor-linux`
2. Run it. On first launch it auto-detects your PalServer, enables the REST
   API in your `PalWorldSettings.ini`, asks for the server admin password and a
   web password, and writes `settings.yaml`.
3. Open http://localhost:8213 in your browser and log in.

### From source

```bash
uv sync             # install deps
uv run poe start    # same interactive first-run setup
```

## Using the dashboard

- Watch server state and players: name, Steam ID, level, online/offline.
- Start or stop the server manually.
- Kick, ban or unban players, including bans already stored by PalServer.

## Configuration

After first run, a `settings.yaml` file is saved next to the app — the folder
holding `palworld-monitor.exe`, the project root when running from source. You
can edit it to change ports, polling rate, auto-stop delay, etc.

`--app-dir PATH` (or `PALWORLD_MONITOR_APP_DIR`) moves that whole app folder
when you want the app and its data somewhere else — a LinuxGSM instance folder,
a data partition. `settings.yaml`, logs, player data and pid files all live
there; the session key is written next to `settings.yaml`, so with `--settings`
it follows that file. If you keep the app folder inside the repository, add it
to `.gitignore` yourself.

Upgrading from an older version with your `settings.yaml` in `src/`? The app
keeps reading it and prints where to move it.

| Setting (YAML) | CLI flag | Description |
|-----------------|----------|-------------|
| — | `--app-dir PATH` | Folder the app keeps its files in (default: next to the app) |
| — | `--settings PATH` | Path to an alternate `settings.yaml` |
| `palserver.exePath` ⚑ | `--exe-path PATH` | Full path to PalServer executable (or, with LGSM, the LGSM instance script) |
| `palserver.adminPassword` ⚑ | `--admin-password PASS` | Admin password from your server's `PalWorldSettings.ini` |
| `web.password` ⚑ | `--web-password PASS` | Password for the web admin interface |
| `palserver.host` | `--host HOST` | Palworld server host (default `127.0.0.1`) |
| `palserver.port` | `--server-port PORT` | Palworld game server port (default `8211`); also watched for incoming connections |
| `palserver.restPort` | — | PalServer REST API port (default `8212`) |
| `palserver.pollingRate` | `--polling-rate SEC` | Seconds between status polls (default `5`) |
| `palserver.useLGSM` | `--use-lgsm` | Manage the server through a LinuxGSM script (Linux only; see below) |
| `web.enabled` | `--no-web` | Disable the web admin UI |
| `web.port` | `--web-port PORT` | Web admin UI port (default `8213`) |
| `web.username` | `--web-username USER` | Web admin username (default `admin`) |
| `web.controlServer` | — | Enable server start/stop controls in the web UI |
| `web.showServerIP` | — | Show the server IP address in the web UI |
| `features.autoStart` | `--no-auto-start` | Disable auto-start when a player connects |
| `features.autoStop` | `--no-auto-stop` | Disable auto-stop when the server is empty |
| `features.playerTracking` | — | Track online/offline players. Important: for banning to work properly, `Pal/Saved/SaveGames/banlist.txt` must be writable by the account running the monitor |
| `autoStop.stopDelay` | — | Seconds of an empty server before auto-stop (default `600`) |
| `security.rateLimitEnabled` | — | Enable request rate limiting |
| `logging.dir` | `--log-dir PATH` | Folder for log files (default: `logs/` inside the app folder) |
| `logging.maxSize` | — | Size of one log file before it rotates (default 5 MB) |
| `logging.backupCount` | — | Rotated log files to keep (default 10) |
| `logging.rotate` | — | Turn log rotation off (default `true`) |
| `logging.compress` | — | Turn backup compression off (default `true`) |
| `logging.retentionDays` | — | Days of old logs to keep (default 30, `0` keeps them all) |

⚑ = required on first run. Passwords are masked in logs. Most settings have a
CLI flag too (run with `--help`); the `logging.*` settings are file-only.

### Logs

The app keeps its logs in a `logs` folder inside its app folder (see
[Configuration](#configuration)): next to the app itself in a release, the
project root when running from source. The console shows the same log lines, so
under systemd `journalctl` works too.

Nothing to maintain: history is kept across restarts, old entries are
compressed and pruned on their own (at startup, on each rotation, and hourly),
and the folder stays small even after months unattended. Want more detail?
Start with `LOG_LEVEL=DEBUG`. The table above covers the knobs if you want to
keep logs longer, larger, or in a different folder.

## LinuxGSM (LGSM) support

If your server is managed by [LinuxGSM](https://linuxgsm.com/lgsm/pwserver/)
rather than a bare `PalServer.sh`, set `useLGSM: true` and point `exePath` at
the LGSM instance script instead of the game binary:

```yaml
palserver:
  exePath: "/home/gameserver/pwserver/pwserver"
  useLGSM: true
  ...
```

With `useLGSM` enabled, the monitor calls the LGSM script's own `start` and
`stop` commands instead of spawning or killing the PalServer process directly,
keeping LGSM's state tracking and `monitor` cron job in sync. Player and
auto-stop detection still work, since they scan OS processes for `PalServer`.
Run the monitor as the same Linux user that owns the LGSM installation, and make
sure the script is executable and reachable at the configured path.

LGSM keeps an instance's files together, so point the monitor at it: run it
with `--app-dir /home/gameserver/pwserver` and its settings, logs, player data,
and pid files all live with that instance. LGSM's own `log/` folder is
untouched.

## Running as a service (Linux)

To survive reboots and restart on crashes, run it under systemd. An example unit
is provided at [`deploy/palworld-monitor.service`](deploy/palworld-monitor.service).

Before installing it, run `palworld-monitor` manually once and complete the
interactive first-run setup — `settings.yaml` must already exist, since
systemd has no terminal for the setup wizard to prompt on. Run the binary you
are about to install (the one in `dist/`, in the default layout) so its
`settings.yaml` lands in that binary's app folder; if you keep the settings
elsewhere, pass `--settings` or `--app-dir` in `ExecStart` instead.

```bash
sudo cp deploy/palworld-monitor.service /etc/systemd/system/
sudo nano /etc/systemd/system/palworld-monitor.service   # edit User / WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now palworld-monitor
journalctl -u palworld-monitor -f   # follow logs
```

`User` must be the same Linux user that owns your PalServer/LGSM
installation — the process needs permission to launch and signal the game
server. The app's own files (`settings.yaml`, logs, `data/players.json`, the
`.pid` files, `session_secret.key`) live in its app folder, which is the
folder holding the binary unless you pass `--app-dir`, so `WorkingDirectory`
no longer decides where they land. Add `--app-dir /home/gameserver/pwserver`
to `ExecStart` to keep them with an LGSM instance.

## Manual PalServer setup (optional)

The app auto-configures your PalServer on first run. This section is only needed
if auto-setup fails or you prefer to configure it yourself.

Your PalServer needs the REST API enabled in `PalWorldSettings.ini`:

```ini
RESTAPIEnabled=True
RESTAPIPort=8212
AdminPassword=your_strong_admin_password
```

<details>
<summary>Where to find PalWorldSettings.ini</summary>

Look inside your PalServer folder. The exact path depends on how you installed it:

**Steam (most common):**

```
C:\Program Files (x86)\Steam\steamapps\common\PalServer\Pal\Saved\Config\WindowsServer\PalWorldSettings.ini
```

**SteamCMD (dedicated server tool):**

```
C:\Program Files\PalServer\Pal\Saved\Config\WindowsServer\PalWorldSettings.ini
```

If `PalWorldSettings.ini` doesn't exist, copy `DefaultPalWorldSettings.ini`
from the same folder and rename it.
</details>

## License

MIT

## Attribution

This project is a modified version of the original PalWorld Dedicated Server Auto-Start-Stop script by nomomo.
Special thanks to nomomo for the original implementation. Buy them a coffee at the following link:

<a href="https://www.buymeacoffee.com/nomomo" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-yellow.png" alt="Buy nomomo A Coffee" height="60"></a>
