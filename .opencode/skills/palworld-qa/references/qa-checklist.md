# Palworld Monitor QA catalog

Living catalog. Update it before testing changed behavior, then mark it verified
with `scripts/qa_delta.py --mark-verified`.

## Baseline happy path

Run these every QA pass.

| ID | Area | Scenario | Steps | Expected |
|----|------|----------|-------|----------|
| H1 | web | Login and dashboard load | Open `/login`; enter QA credentials | Login redirects to `/`; dashboard renders; no console error |
| H2 | lifecycle/web | Initial stopped state | Load `/` with no server | Status OFF; Start visible; Stop hidden; player list shows no online players |
| H3 | lifecycle/web | Start from the dashboard | Click Start Server | STARTING appears, then RUNNING/ON; dummy roster appears; Stop visible, Start hidden |
| H4 | web/SSE | Committed snapshot | Call getStatus twice via the page | `state` is RUNNING; `playerData` is `ok`; `revision` is monotonic; players match the dashboard |
| H5 | players | Kick a player | Kick TestPlayer1 | Success alert; player becomes offline; other players stay online |
| H6 | bans | Ban a player | Ban TestPlayer2 | Success alert; row shows Banned and Unban; banned list contains the ID |
| H7 | bans | Unban a player | Unban TestPlayer2 | Success alert; row returns to offline/normal actions; banned list no longer contains the ID |
| H8 | lifecycle/web | Stop from the dashboard | Click Stop Server | STOPPING appears, then OFF/STOPPED; tracked players are offline; Start visible, Stop hidden |
| H9 | lifecycle | Restart | Start again after stop | STARTING then RUNNING; fresh roster; no ghost players from the previous generation |
| H10 | auto-stop UI | Notice visibility | Run once with autoStop disabled and once enabled | Disabled: no notice. Enabled + RUNNING: notice visible. Enabled + STARTING/STOPPING/STOPPED: hidden |

## Edge cases

Run the ones matching changed areas, plus at least two unrelated ones.

| ID | Area | Scenario | Steps | Expected |
|----|------|----------|-------|----------|
| E1 | lifecycle | Start while already running | Start a second time via `/action` | Response stays RUNNING; no second PalServer process |
| E2 | lifecycle | Stop while already stopped | Stop a second time via `/action` | Response stays STOPPED; HTTP 200; no error state |
| E3 | auto-start | UDP probe starts the server | With the app STOPPED, send `09 08 00` to UDP 8211 | Server starts; dashboard goes RUNNING |
| E4 | process/lifecycle | External crash | Kill the dummy process while RUNNING | Dashboard goes OFF; players offline; auto-start listener re-arms |
| E5 | REST/lifecycle | REST unavailable, process alive | Make REST unavailable with a running dummy; enable auto-stop and wait past guard+delay | State stays RUNNING; `playerData=unavailable`; last-known roster preserved with an unavailable warning, not replaced by an authoritative empty roster; no auto-stop |
| E6 | auto-stop | Empty roster auto-stops | Dummy with `DUMMY_PLAYER_COUNT=0`; autoStop enabled; wait past guard+delay | Server stops; dashboard OFF; dummy process gone |
| E7 | web/SSE | Stale snapshot ignored | Apply an older revision snapshot in page JS | Dashboard does not regress; no stale player appears |
| E8 | auth | Invalid login | One wrong password attempt | Error shown; session not created; do not exceed the lockout limit |
| E9 | auth | Logout | Click Logout | Redirected to login; protected page requires login again |
| E10 | bans | Ban persists across reload | Ban a player, reload `/` | Banned player still shows Banned/Unban after reload |
| E11 | lifecycle | Rapid start/stop | Start, then stop as soon as RUNNING; start again after cooldown | Transitions remain ordered; no duplicate process; no stuck STARTING/STOPPING |
| E12 | lifecycle | Failed stop recovery | Use a fake/forced termination failure or LGSM pending path if available | Failed stop returns to RUNNING and polling resumes; pending LGSM remains STOPPING until exit |
| E13 | lifecycle/threads | Lean worker set | Inspect runtime thread names while stopped and running | Exactly four lifecycle threads: lifecycle, detection, poll, process; no separate launch/stop workers or LGSM watchdog |
| E14 | auto-start/threads | Listener re-arms without leaking | Repeat UDP start, dashboard stop, and UDP start after cooldown | One listener while STOPPED; none while RUNNING; starts succeed without duplicate listeners or processes |
| E15 | lifecycle/threads | Blocked REST does not block control or detection | Inject a blocked REST poll; stop or crash the dummy before it returns | STOPPING/STOPPED commits remain responsive; late roster cannot revive players or regress state |
| E16 | QA cleanup | No dummy servers left behind | After QA, stop the owned monitor and all verified dummy processes, including pre-existing leftovers and launcher children; rescan processes and ports | Zero dummy processes; no QA-owned TCP/UDP listeners; unrelated processes untouched |

## Change-sensitive map

When `qa_delta.py` reports an area, update and run the matching scenarios.

| Changed area | Scenarios |
|--------------|-----------|
| lifecycle, process control, auto-start/auto-stop | H2–H4, H8–H10, E1–E7, E11–E15 |
| web server, templates, static JS/CSS | H1–H10, E7, E9 |
| player/ban managers, REST client | H5–H7, E5, E10 |
| settings, CLI, setup, main composition | H1–H3, H10, E3, E5, E6 |
| docs only | no new browser scenario; spot-check the affected instructions |

Run E16 after every QA pass, including failed or interrupted runs.

## Not browser-testable here

- Real LinuxGSM shutdown and detection of delayed exits.
- Linux process backend and real PalServer process behavior.
- Destructive e2e/smoke suites.

Report these as unverified risk; unit tests cover the mocked paths.

## Catalog maintenance

- Add a scenario when a new user-visible feature or lifecycle state appears.
- Update expected results when behavior changes.
- Remove scenarios that no longer describe the app.
- Keep IDs stable; use the next free H/E number.
- Keep run results and verification notes out of this catalog. Record local
  verification state with `scripts/qa_delta.py --mark-verified`; it is ignored by Git.
