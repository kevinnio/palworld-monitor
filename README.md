# PalWorld Monitor

![CI](https://github.com/kevinnio/palworld-monitor/actions/workflows/ci.yml/badge.svg)

A Python application for managing PalWorld dedicated servers on Windows and Linux. The server automatically starts when players attempt to connect and shuts down when idle, with a web-based admin interface for monitoring and control.

## Features

- **Cross-platform support**: Windows and Linux compatibility, including servers managed by [LinuxGSM](https://linuxgsm.com/) (LGSM)
- **Automatic server management**:
  - Auto-start when players attempt to connect
  - Auto-stop after configured delay when server is empty (default: 10 minutes)
  - Supports REST API communication with PalServer
- **Web-based admin interface**:
  - Real-time server monitoring
  - Server control capabilities
  - Light/dark theme support
- **Player management**:
  - Track online/offline players (session-based)
  - Kick or ban players
  - Player details: name, Steam ID, level
  - Unban players from the dashboard, including bans already stored by PalServer
- **Security**:
  - Web interface with authentication (username/password login)
  - CSRF protection and rate limiting
  - Login attempt lockout after failed attempts
- **Configurable settings**: YAML-based configuration file

## Limitations

- **Development stage**: This tool is in active development and may contain bugs
- **Same host requirement**: In order to be able to start/stop the Palworld Server this app must run on the same host as the PalWorld server (your own PC, same AWS EC2 instance, same Docker container, etc).

## Quick Start

1. Download from the latest [GitHub Release](https://github.com/kevinnio/palworld-monitor/releases):
   - **Windows:** `palworld-monitor-windows.exe`
   - **Linux:** `palworld-monitor-linux`
2. Run it. On first launch it auto-detects your PalServer, enables the REST
   API in your `PalWorldSettings.ini`, asks for a couple of passwords, and
   starts.
3. Open http://localhost:8213 in your browser and log in with the password you
   chose.

No config files to edit. The monitor auto-starts and auto-stops your PalServer
as players come and go.

### From source

```bash
uv sync             # install deps
uv run poe start    # same interactive first-run setup
```

Run e2e smoke tests with `uv run poe smoke`. **Do not run smoke tests
while a real PalServer is running** — they kill all PalServer processes
and occupy game ports to isolate the test environment.

## Configuration

After first run, a `settings.yaml` file is saved next to the app.  You can edit
it to change ports, polling rate, auto-stop delay, etc.

| Setting (YAML) | CLI flag | Description |
|-----------------|----------|-------------|
| — | `--settings PATH` | Path to an alternate `settings.yaml` |
| `palserver.exePath` ⚑ | `--exe-path PATH` | Full path to PalServer executable (or, with LGSM, the LGSM instance script) |
| `palserver.adminPassword` ⚑ | `--admin-password PASS` | Admin password from your server's `PalWorldSettings.ini` |
| `palserver.pollingRate` ⚑ | `--polling-rate SEC` | Seconds between status polls (default `5`) |
| `palserver.useLGSM` | `--use-lgsm` | Manage the server through a LinuxGSM script instead of launching it directly (Linux only) |
| `web.password` ⚑ | `--web-password PASS` | Password for the web admin interface |
| `palserver.host` | `--host HOST` | Palworld server host |
| `palserver.port` | `--server-port PORT` | Palworld game server port |
| `palserver.protocol` | `--protocol REST` | Server communication protocol |
| `web.enabled` | `--no-web` | Disable the web admin UI |
| `web.port` | `--web-port PORT` | Web admin UI port |
| `web.username` | `--web-username USER` | Web admin username |
| `features.autoStart` | `--no-auto-start` | Disable auto-start on UDP probe |
| `features.autoStop` | `--no-auto-stop` | Disable auto-stop when server is empty |

⚑ = required on first run.  Passwords are masked in logs.  Every setting can be
overridden via CLI flag (run with `--help`).

## LinuxGSM (LGSM) support

If your server is managed by [LinuxGSM](https://linuxgsm.com/lgsm/pwserver/)
rather than a bare `PalServer.sh`, set `useLGSM: true` and point `exePath` at
the LGSM instance script instead of the game binary:

```yaml
palserver:
  exePath: "/home/gameserver/pwserver/pwserver"
  useLGSM: true
  adminPassword: "changeme"
  ...
```

(or `--use-lgsm --exe-path /home/gameserver/pwserver/pwserver` on the CLI.)

With `useLGSM` enabled, the monitor calls the LGSM script's own `start` and
`stop` commands instead of spawning or killing the PalServer process
directly. This keeps LGSM's own state tracking in sync — including its
`monitor` cron job, which would otherwise think a monitor-initiated stop was
a crash and restart the server. Player/auto-stop detection is unaffected
since it works by scanning OS processes for `PalServer`, regardless of how
it was launched.

Requirements:

- `palworld-monitor` must run as the same Linux user that owns the LGSM
  installation (it needs permission to run the script and to see/signal the
  server process).
- The LGSM script must be executable and reachable at the configured path.

## Running as a service (Linux)

To have `palworld-monitor` survive reboots and restart on crashes, run it
under systemd rather than in a terminal. An example unit is provided at
[`deploy/palworld-monitor.service`](deploy/palworld-monitor.service).

Before installing it, run `palworld-monitor` manually once and complete the
interactive first-run setup — `settings.yaml` must already exist, since
systemd has no terminal for the setup wizard to prompt on.

```bash
sudo cp deploy/palworld-monitor.service /etc/systemd/system/
sudo nano /etc/systemd/system/palworld-monitor.service   # edit User / WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now palworld-monitor
journalctl -u palworld-monitor -f   # follow logs
```

`User` must be the same Linux user that owns your PalServer/LGSM
installation (same requirement as [LGSM support](#linuxgsm-lgsm-support)
above) — the process needs permission to launch and signal the game
server. `WorkingDirectory` matters too: `app.log`, `data/players.json`,
`banlist.txt`, the `.pid` files, and `session_secret.key` are all written
relative to it, not to wherever the binary itself lives.

The dashboard reads PalServer's `Pal/Saved/SaveGames/banlist.txt`, including
native `steam_id,player_uid` entries. Ban and unban require the server's REST API
to be available. Keep the banlist writable by the account running the monitor.

## Manual PalServer setup (optional)

The app auto-configures your PalServer on first run.  This section is only
needed if auto-setup fails, or if you want to configure things yourself.

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
