---
name: Palworld QA
description: Manually QA the Palworld Monitor app in a browser and keep its test catalog current. Use whenever the user says QA, test the app/dashboard, verify a feature or behavior, or asks for a happy-path/edge-case pass, especially after changes to lifecycle, web/SSE, process control, players/bans, settings, or auto-start/auto-stop. Prefer the integrated browser; fall back to Chrome only when no integrated browser is available.
---

# Palworld Monitor QA

QA this app against a running instance, not just unit tests. The goal is to
exercise what a user sees and does, while keeping a living scenario catalog that
grows when the app's behavior changes.

## 1. Pick the browser

Use the integrated browser first: the current environment's browser panel or
built-in browser tools. Use its open/snapshot/click/type actions.

Only if no integrated browser is available, use Chrome through a Chrome
automation extension. Load its browser skill first if one exists. Create a new
tab, and close only tabs you created. Do not reuse the user's existing tabs
unless they asked for it.

If no browser can be reached, stop and tell the user; do not substitute API-only
checks for UI QA. API calls are useful supplements, not a replacement.

## 2. Prepare a disposable instance

Read `references/environment.md` before starting anything. It contains the temp
settings, dummy PalServer, startup, cleanup, and dialog-handling details.

Rules that matter:

- Never kill or disturb a real PalServer. Check ports/processes first.
- Do not run `uv run poe smoke` or any e2e suite unless the user explicitly asks;
  those tests are destructive.
- Use a temporary settings file and the dummy PalServer. Stop leftover dummy
  servers before testing so discovery cannot adopt an old instance.
- Always stop all remaining verified dummy servers when finished, including
  leftovers from earlier runs and Windows launcher shims/children. Stop only the
  monitor instance you started. Verify no dummy processes or QA port owners
  remain before deleting temp data and closing your browser tab.
- Log in with the web credentials you set in the temp settings.

## 3. Refresh the QA catalog before testing

The catalog is `references/qa-checklist.md`. It is living documentation, not a
one-time checklist.

From the project root, run:

```bash
uv run python .agents/skills/palworld-qa/scripts/qa_delta.py
```

This compares the current tree (including uncommitted work) with the last
verified commit and prints the changed files grouped by area. If there is no
state yet, it says so.

For every changed area:

1. Read the relevant code/docs (`AGENTS.md`, `README.md`, routes, lifecycle
   modules, settings, tests).
2. Update the catalog before running scenarios:
   - Add scenarios for new features or behavior.
   - Adjust expected results when behavior changed.
   - Remove scenarios that no longer describe the app.
   - Update the change-sensitive scenario map.
3. If a change touches lifecycle, web/SSE, process control, players/bans,
   settings, or auto-start/auto-stop, run the matching scenarios in addition to
   the baseline.

Do not let the catalog drift: a feature change without a catalog update means the
QA is incomplete.

## 4. Choose the run set

Every QA run includes the baseline happy path from the catalog. Add:

- all scenarios for changed areas;
- at least two edge cases not directly tied to the change, to catch collateral
  regressions;
- any scenario the user explicitly asked about.

If the change is very small, a focused baseline plus the matching area and one
unrelated edge case is enough. If the change touches lifecycle/process/web state,
run the full happy path and the full edge set.

## 5. Execute and observe

For each scenario:

- Record the scenario ID, what you did, and what you saw.
- Verify the committed state, not just the button response. For example:
  - STARTING must become RUNNING only with an observed process.
  - STOPPING must become STOPPED only after confirmed exit.
  - REST unavailable must show degraded/unknown player data, not an authoritative
    empty roster.
  - Auto-stop must require a fresh empty sample.
- Prefer the page's own buttons and flows. For kick/ban/stop confirmations,
  override `window.confirm` and `window.alert` in page JS before clicking, or use
  the page's fetch/API path with the CSRF token. Never leave a native modal open.
- Check the browser console for app errors when something looks wrong.
- Use `references/qa-checklist.md` for expected results; do not invent a pass.

## 6. Report

Return a compact table:

| ID | Scenario | Result | Evidence |
|----|----------|--------|----------|

Include:

- setup used (temp settings, dummy mode, browser);
- failures with the observed state/event/revision, not just "failed";
- new or changed scenarios added to the catalog;
- any risk not covered (LGSM/Linux, real PalServer, e2e).

Report run results in chat. Keep any saved evidence in the OS temp directory or
the skill's ignored `results/` directory, not in committed files.

## 7. Update the catalog and mark verified

After the run:

1. Update `references/qa-checklist.md` with reusable new/refined scenarios only.
   Do not add run outcomes, dates, commit IDs, or verification notes.
2. Mark the current commit verified in the ignored local
   `references/qa-state.json` file:

```bash
uv run python .agents/skills/palworld-qa/scripts/qa_delta.py --mark-verified
```

3. Clean up per `references/environment.md`.

The next run will then highlight only newer changes, so the catalog stays current
as features and behavior evolve.

## Guardrails

- QA against a running app; unit tests alone are not this skill.
- Do not run destructive e2e/smoke suites.
- Do not disturb a real PalServer or the user's browser tabs.
- Do not hide a failure behind a passing happy path.
- Never commit QA results, screenshots, logs, or local verification state. Commit
  only reusable skill instructions, scenarios, and supporting tools.
- Do not leave the QA app, any verified dummy servers, temp data, or browser tab
  running, even after a failed or interrupted scenario. Never substitute a broad
  Python/PalServer process kill for exact dummy-script identification.
