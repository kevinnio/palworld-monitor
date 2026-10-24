# QA environment

Use a disposable app instance and the dummy PalServer. The dummy exposes only
the REST API (port 8212); the monitor starts it as a normal process.

## Preconditions

- Run from the project root.
- Ports 8211, 8212, and 8213 are free.
- No real PalServer is running. Do not kill one.
- `uv sync` has been run, so `.venv` exists.

Check first:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8211,8212,8213 -ErrorAction SilentlyContinue |
  Select-Object LocalPort,OwningProcess
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*PalServer*' -or $_.CommandLine -like '*src.main*' } |
  Select-Object ProcessId,CommandLine
```

On Linux/macOS, use the equivalents:

```bash
ss -ltnp | grep -E ':(8211|8212|8213)\b' || true
ps aux | grep -E 'src\.main|PalServer' | grep -v grep || true
```

If a real PalServer or another monitor instance is present, stop and ask the
user before doing anything destructive.

## Temp settings

Create a temp directory and a settings file there. Resolve absolute paths and
avoid spaces in paths because the launcher splits `exeArguments` on whitespace.

```yaml
palserver:
  exePath: "<project>/.venv/Scripts/python.exe"   # .venv/bin/python on Linux
  exeArguments: "<project>/test/e2e/dummy/PalServer-Dummy.py"
  host: "127.0.0.1"
  port: 8211
  adminPassword: "palworld123"   # must match test/e2e/dummy/PalServer-Dummy.py
  useLGSM: false
  protocol: "REST"
  restPort: 8212
  pollingRate: 2

web:
  enabled: true
  port: 8213
  username: "admin"
  password: "<choose-a-throwaway-password>"
  controlServer: true
  showServerIP: false

features:
  playerTracking: true
  autoStart: true       # false for direct web-start/auto-stop runs
  autoStop: false       # true for auto-stop/notice runs

autoStop:
  stopDelay: 5

security:
  sessionSecretKey: "qa-only-secret-key-12345678901234567890"
  sessionTimeout: 3600
  maxLoginAttempts: 5
  lockoutDuration: 300
  rateLimitEnabled: true
  rateLimitRequests: 1000
  rateLimitWindow: 60
```

Set a throwaway web password in the temp settings and use it to log in. Do not
reuse a real account password.

Variants:

- **Base / happy path**: `autoStart: true`, `autoStop: false`.
- **Degraded REST**: same, but `restPort: 8299` (nothing listens), `autoStart:
  false`, `autoStop: true`. Start a dummy first; the monitor should adopt it and
  stay RUNNING with `playerData=unavailable`.
- **Auto-stop**: `restPort: 8212`, `autoStart: false`, `autoStop: true`,
  `stopDelay: 5`. Start the dummy with `DUMMY_PLAYER_COUNT=0`, let the monitor
  adopt it, and wait past the 30 s startup guard + delay.

## Start the app

Windows:

```powershell
$env:PALWORLD_MONITOR_SETTINGS = "<temp>/settings.yaml"
$app = Start-Process -FilePath "<project>\.venv\Scripts\python.exe" `
  -ArgumentList "-m","src.main" -WorkingDirectory "<project>" `
  -WindowStyle Hidden -PassThru
$app.Id
```

Linux/macOS:

```bash
PALWORLD_MONITOR_SETTINGS=<temp>/settings.yaml \
  .venv/bin/python -m src.main &
echo $!
```

Wait for `http://localhost:8213/login` to answer. Open the app with the chosen
browser and log in with the web username/password you set.

The dummy can be started manually for adoption/degraded runs:

```powershell
$env:DUMMY_PLAYER_COUNT = "0"   # omit for the default 3 players
Start-Process -FilePath "<project>\.venv\Scripts\python.exe" `
  -ArgumentList "test/e2e/dummy/PalServer-Dummy.py" `
  -WorkingDirectory "<project>" -WindowStyle Hidden -PassThru
```

Linux/macOS:

```bash
DUMMY_PLAYER_COUNT=0 .venv/bin/python test/e2e/dummy/PalServer-Dummy.py &
echo $!
```

On Windows the venv `python.exe` shim spawns a real uv-python child. Two
`PalServer-Dummy.py` processes mean one server, not a duplicate.

## Browser dialogs

Kick, ban, and stop use `confirm()` and then `alert()`. Native modals block
browser automation. Before using those buttons, run page JS:

```js
window.__qaAlerts = [];
window.confirm = () => true;
window.alert = (message) => window.__qaAlerts.push(String(message));
```

Alternatively, use the page's own fetch/API path with the CSRF token from
`meta[name="csrf-token"]` and inspect the returned snapshot.

## Cleanup

Stop only the processes started for QA, identified by their command line:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*src.main*' -or $_.CommandLine -like '*PalServer-Dummy.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

Linux/macOS (check the matches first, then stop only those PIDs):

```bash
pgrep -af 'src\.main|PalServer-Dummy\.py'
pkill -f 'src\.main'
pkill -f 'PalServer-Dummy\.py'
```

Then remove QA artifacts created in the project:

- `data/players.json` (and `data/` if empty)
- `.venv/Scripts/banlist.txt` (or the configured banlist path)
- `app.log`
- stale PID files: `palworld_server.win.pid`, `palworld_server.linux.pid`,
  `palworld_server.lgsm.pid`
- the temp settings directory

Close the browser tab you opened. Do not run `uv run poe smoke` or any e2e suite
unless the user explicitly asks.
