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
| E4 | process/lifecycle | External crash | Kill the dummy process while RUNNING; on Linux include an exited child that has not yet been reaped | Dashboard goes OFF; players offline; auto-start listener re-arms; a zombie is not considered a running server or adopted as a replacement |
| E5 | REST/lifecycle | REST unavailable, process alive | Make REST unavailable with a running dummy; enable auto-stop and wait past guard+delay | State stays RUNNING; `playerData=unavailable`; last-known roster preserved with an unavailable warning, not replaced by an authoritative empty roster; no auto-stop |
| E6 | auto-stop | Empty roster auto-stops | After monitor startup cleanup, launch a dummy with `DUMMY_PLAYER_COUNT=0`; confirm it is alive and RUNNING with a healthy empty roster; wait past guard+delay without sending stop | Committed states progress through STOPPING to STOPPED; the owned dummy exits only after the startup guard; a setup kill or crash cannot count as a pass |
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
| E17 | bans/SSE | Ban state across open dashboards | Open two authenticated dashboards; ban a tracked player in one, then unban from the other | Both dashboards show Banned/Unban after the ban and normal offline actions after unban without a reload; `/banned` agrees |
| E18 | bans | Native pair entries | Seed a disposable banlist with `steam_id,player_uid` entries and comments; ban, reload, unban one entry | One row per Steam ID; unban removes only the target entry; unrelated pairs and comments stay intact |
| E19 | bans | Rejected and failed moderation | Submit an invalid ID through the page fetch path; cancel a confirmation; make the banlist unwritable | Invalid IDs get HTTP 400 before the API call; cancel sends nothing; storage failure shows a failure alert and keeps visible ban state |
| E20 | bans/live server | Live PalServer moderation | With a real PalServer running and auto-stop disabled, ban then unban an agreed synthetic ID through the built app | REST returns 200; the native `Pal/Saved/SaveGames/banlist.txt` gains then loses only that ID; pre-existing bans and players are untouched; the server keeps running |
| E21 | lifecycle | Startup adoption with stale state | Leave a dummy running, plus a PID file naming a dead PID and persisted online players; start the monitor | Adopts the live dummy as RUNNING without launching a second process; stale PID and ghost roster are cleared |

## Change-sensitive map

When `qa_delta.py` reports an area, update and run the matching scenarios.

| Changed area | Scenarios |
|--------------|-----------|
| lifecycle, process control, auto-start/auto-stop | H2–H4, H8–H10, E1–E7, E11–E15 |
| web server, templates, static JS/CSS | H1–H10, E7, E9 |
| player/ban managers, REST client | H5–H7, E5, E10, E17–E20 |
| settings, CLI, setup, main composition | H1–H3, H10, E3, E5, E6 |
| docs only | no new browser scenario; spot-check the affected instructions |

Run E16 after every QA pass, including failed or interrupted runs.

## Automated coverage and manual checks

Coverage below describes existing assertions, not run results. Related unit or
integration tests do not replace every visible result in a QA scenario.

| Scenarios | Automated coverage | Still check manually |
|-----------|--------------------|----------------------|
| H1, E8, E9 | Unit auth rules; integration login/failure/logout routes; E2E browser login and dashboard rendering | Browser errors, invalid-login feedback, logout protection |
| H2, H3, H8, E1, E2 | Unit transitions/guards; integration controller/process/route behavior; E2E browser start/stop | Transitional labels, correct button visibility, duplicate start/stop requests through the live web routes |
| H4, E7 | Unit server snapshots/revision ordering; integration payloads; auto-stop E2E reads committed SSE states | Dashboard consistency across refreshes and rejection of an older revision in page JavaScript |
| H5, H6, H7, E10 | Unit API calls/ban storage; integration action routes and dummy API; E2E browser kick/ban reduces online counts | Alerts, exact row/banned-list state, browser unban and ban persistence after reload |
| H9, E4, E11, E14 | Unit listener behavior and zombie rejection; integration restarts, process replacement, stale generations, thread cleanup and a real unreaped Linux child | Full crash recovery and repeated UDP start/web stop/restart cycle without leaked listeners |
| H10 | Integration initial HTML checks when stopped, running and auto-stop disabled | Live notice visibility through STARTING, RUNNING, STOPPING and STOPPED |
| E3 | Unit packet/listener behavior; E2E sends a real UDP packet and checks process discovery | Dashboard reaches RUNNING and listener releases the game port |
| E5, E15 | Unit REST failure/policy rules; integration blocked I/O, roster preservation and late-response rejection | Degraded dashboard warning and live control/detection while REST is unavailable or blocked |
| E6 | Unit deadline/freshness policy; integration controller revalidation; E2E healthy empty dummy, startup guard, STOPPING/STOPPED and confirmed exit | Dashboard OFF and notice visibility after the automatic stop |
| E12, E13 | Unit stop/state contracts; integration four-worker lifecycle, failed stops, and delayed LGSM stand-in shutdown | Fault-path behavior with the deployed process backend |
| E16 | Integration fixtures clean up owned processes; E2E fixtures perform cleanup | Rescan exact dummy processes and QA-owned ports, including launcher children; verify unrelated processes were untouched |

Test locations and commands:

- Unit tests: `test/test_*.py`. External services are generally mocked, although some tests use real files, sockets or threads.
- Integration tests: `test/integration/`. Includes real native process launches, a Linux-only executable LGSM stand-in, dummy REST API contracts, controller/runtime wiring and Flask request flows. Run with `uv run poe test test/integration`.
- E2E tests: `test/e2e/`. Run the full monitor with the dummy server, including Playwright browser login, start/stop and kick/ban, plus UDP auto-start and empty-server auto-stop. Run with `uv run poe smoke` only when explicitly requested and after the environment safety checks.
- `uv run poe test` runs unit and integration tests together. CI runs both this command and the E2E suite on Windows and Ubuntu.

## Platform coverage and limits

- Browser E2E scenarios run on Windows and Linux. Timing failures should be diagnosed and fixed with bounded condition waits, not hidden by a blanket Linux skip.
- Native process contracts run only on their matching OS. The LinuxGSM stand-in requires Linux executable-script support, so it skips on other platforms.
- LGSM integration skips when an existing PalServer is detected. Dummy API and LGSM integration skip if their required REST port is unavailable, rather than contacting or stopping another service.
- The LGSM stand-in checks our real command invocation, process discovery, adoption, failure handling and delayed exit. It does not verify upstream LinuxGSM, tmux, cron monitoring or actual game shutdown behavior.
- The dummy REST API is not a real Palworld server. Real-game behavior and a real LinuxGSM installation remain separate host-level validation.
- E2E helpers are destructive. A passing suite is not proof of E16's isolation requirements. Never run them against a real server or unrelated port owner.

## Catalog maintenance

- Add a scenario when a new user-visible feature or lifecycle state appears.
- Update expected results when behavior changes.
- Remove scenarios that no longer describe the app.
- Keep IDs stable; use the next free H/E number.
- Keep run results and verification notes out of this catalog. Record local
  verification state with `scripts/qa_delta.py --mark-verified`; it is ignored by Git.
