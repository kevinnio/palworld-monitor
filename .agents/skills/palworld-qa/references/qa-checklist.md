# Palworld Monitor QA catalog

Living catalog. Update it before testing changed behavior, then mark it verified
with `scripts/qa_delta.py --mark-verified`.

## Baseline happy path

Run these every QA pass.

| ID | Area | Scenario | Steps | Expected |
|----|------|----------|-------|----------|
| H1 | web | Login and dashboard load | Open `/login`; enter QA credentials | Login redirects to `/`; dashboard renders; no console error |
| H2 | lifecycle/web | Initial stopped state | Load `/` with no server | Status OFF; Start visible; Stop hidden; player list shows no online players |
| H3 | lifecycle/web | Start from the dashboard | Click Start Server | Status changes to ON once the process is detected; dummy roster appears; Stop visible, Start hidden |
| H4 | web/SSE | Committed snapshot | Call getStatus twice via the page | `running` true; `playerCount` matches `players`; `banned_players` matches the banlist; players match the dashboard |
| H5 | players | Kick a player | Kick TestPlayer1 | Success alert; player becomes offline; other players stay online |
| H6 | bans | Ban a player | Ban TestPlayer2 | Success alert; row shows Banned and Unban immediately, even before the next player poll; banned list contains the ID |
| H7 | bans | Unban a player | Unban TestPlayer2 | Success alert; row returns to offline/normal actions immediately; banned list no longer contains the ID |
| H8 | lifecycle/web | Stop from the dashboard | Click Stop Server | Status returns to OFF; tracked players are offline; Start visible, Stop hidden |
| H9 | lifecycle | Restart | Start again after stop | Status ON again; fresh roster; no ghost players from the previous generation |
| H10 | auto-stop UI | Notice visibility | Run once with autoStop disabled and once enabled | Disabled: no notice at all. Enabled + running: notice visible. Enabled + stopped: hidden |

## Edge cases

Run the ones matching changed areas, plus at least two unrelated ones.

| ID | Area | Scenario | Steps | Expected |
|----|------|----------|-------|----------|
| E1 | lifecycle | Start while already running | Start a second time via `/action` | Response stays RUNNING; no second PalServer process |
| E2 | lifecycle | Stop while already stopped | Stop a second time via `/action` | Response stays STOPPED; HTTP 200; no error state |
| E3 | auto-start | UDP probe starts the server | With the app STOPPED, send `09 08 00` to UDP 8211 | Server starts; dashboard goes RUNNING |
| E4 | process/lifecycle | External crash | Kill the dummy process while RUNNING; on Linux include an exited child that has not yet been reaped | Dashboard goes OFF; players offline; auto-start listener re-arms; a zombie is not considered a running server or adopted as a replacement |
| E5 | REST/lifecycle | REST unavailable, process alive | Make REST unavailable with a running dummy; enable auto-stop and wait past guard+delay | State stays running; last-known roster is preserved with an unavailable warning, not replaced by an authoritative empty roster; no auto-stop |
| E6 | auto-stop | Empty roster auto-stops | After monitor startup cleanup, launch a dummy with `DUMMY_PLAYER_COUNT=0`; confirm it is alive and running with a healthy empty roster; wait past guard+delay without sending stop | Status goes OFF; the owned dummy exits only after the startup guard; a setup kill or crash cannot count as a pass |
| E8 | auth | Invalid login | One wrong password attempt | Error shown; session not created; do not exceed the lockout limit |
| E9 | auth | Logout | Click Logout | Redirected to login; protected page requires login again |
| E10 | bans | Ban persists across reload | Ban a player, reload `/` | Banned player still shows Banned/Unban after reload |
| E11 | lifecycle | Rapid start/stop | Start, then stop as soon as ON; start again after cooldown | No duplicate process; no page left stuck showing the wrong status |
| E12 | lifecycle | Failed stop recovery | Force termination to fail, or use the LGSM path if available | Failed stop leaves the server reported as running and polling continues; LGSM shutdown stays pending until the exit is observed |
| E14 | auto-start/threads | Listener re-arms without leaking | Repeat UDP start, dashboard stop, and UDP start after cooldown | One listener while stopped; none while running; starts succeed without duplicate listeners or processes |
| E16 | QA cleanup | No dummy servers left behind | After QA, stop the owned monitor and all verified dummy processes, including pre-existing leftovers and launcher children; rescan processes and ports | Zero dummy processes; no QA-owned TCP/UDP listeners; unrelated processes untouched |
| E17 | bans/SSE | Ban state across open dashboards | Open two authenticated dashboards; ban a tracked player in one, then unban from the other | Both dashboards show Banned/Unban after the ban and normal offline actions after unban without a reload; `/banned` agrees |
| E18 | bans | Native pair entries | Seed a disposable banlist with `steam_id,player_uid` entries and comments; ban, reload, unban one entry | One row per Steam ID; unban removes only the target entry; unrelated pairs and comments stay intact |
| E19 | bans | Rejected and failed moderation | Submit an invalid ID through the page fetch path; cancel a confirmation; make the banlist unwritable | Invalid IDs get HTTP 400 before the API call; cancel sends nothing; storage failure shows a failure alert and keeps visible ban state |
| E20 | bans/live server | Live PalServer moderation | With a real PalServer running and auto-stop disabled, ban then unban an agreed synthetic ID through the built app | REST returns 200; the native `Pal/Saved/SaveGames/banlist.txt` gains then loses only that ID; pre-existing bans and players are untouched; the server keeps running |

## Change-sensitive map

When `qa_delta.py` reports an area, update and run the matching scenarios.

| Changed area | Scenarios |
|--------------|-----------|
| lifecycle, process control, auto-start/auto-stop | H2–H4, H8–H10, E1–E6, E11, E12, E14 |
| web server, templates, static JS/CSS | H1–H10, E9 |
| player/ban managers, REST client | H5–H7, E5, E10, E17–E20 |
| settings, CLI, setup, main composition | H1–H3, H10, E3, E5, E6 |
| docs only | no new browser scenario; spot-check the affected instructions |

Run E16 after every QA pass, including failed or interrupted runs.

## Automated coverage and manual checks

Coverage below describes existing assertions, not run results. Related unit or
integration tests do not replace every visible result in a QA scenario.

| Scenarios | Automated coverage | Still check manually |
|-----------|--------------------|----------------------|
| H1, E8, E9 | Unit auth rules; Flask request tests for login/failure/logout routes; E2E browser login | Browser errors, invalid-login feedback, logout protection |
| H2, H3, H8, E1, E2 | Unit lifecycle guards; controller/process tests; E2E browser start/stop | Button visibility, duplicate start/stop requests through live routes |
| H4 | Unit status payloads; E2E reads dashboard state | Dashboard consistency across manual refreshes |
| H5, H6, H7, E10, E17–E20 | Unit API/ban storage; request tests for moderation routes; E2E browser kick/ban/unban and cross-dashboard updates | Alerts, exact row/banned-list state, two dashboards, reload persistence, native banlist, live server |
| H9, E4, E11, E14 | Unit listener behavior and zombie rejection; E2E restarts and process discovery | Full crash recovery and repeated UDP start/web stop/restart cycle without leaked listeners |
| H10 | Request-level template checks for stopped, running and disabled | Live notice visibility while running and after a stop |
| E3 | Unit packet/listener behavior; E2E sends a real UDP packet and checks process discovery | Dashboard reaches ON and the listener releases the game port |
| E5 | Unit REST failure rules; roster preservation | Degraded dashboard warning while REST is unavailable |
| E6 | Unit guard/delay policy; E2E healthy empty dummy, startup guard and confirmed exit | Dashboard OFF after the automatic stop |
| E12 | Unit stop failures and LGSM pending shutdown | Fault-path behavior with the deployed process backend |
| E16 | E2E fixtures perform cleanup | Rescan exact dummy processes and QA-owned ports, including launcher children; verify unrelated processes were untouched |

Test locations and commands:

- Unit tests: `test/test_*.py`. External services are generally mocked, although some tests use real files, sockets or threads.
- E2E tests: `test/e2e/`. Run the full monitor with the dummy server, including Playwright browser login, start/stop and kick/ban/unban, plus UDP auto-start and empty-server auto-stop. Run with `uv run poe smoke` only when explicitly requested and after the environment safety checks.
- `uv run poe test` runs the unit tests; CI runs both that command and the E2E suite on Windows and Ubuntu.

## Platform coverage and limits

- Browser E2E scenarios run on Windows and Linux. Timing failures should be diagnosed and fixed with bounded condition waits, not hidden by a blanket Linux skip.
- Native process contracts run only on their matching OS. LGSM unit tests mock the script, so a real LinuxGSM installation remains host-level validation.
- The dummy REST API is not a real Palworld server. Real-game behavior needs E20 against a live server.
- E2E helpers are destructive. A passing suite is not proof of E16's isolation requirements. Never run them against a real server or unrelated port owner.

## Catalog maintenance

- Add a scenario when a new user-visible feature or lifecycle state appears.
- Update expected results when behavior changes.
- Remove scenarios that no longer describe the app.
- Keep IDs stable; use the next free H/E number.
- Keep run results and verification notes out of this catalog. Record local
  verification state with `scripts/qa_delta.py --mark-verified`; it is ignored by Git.
