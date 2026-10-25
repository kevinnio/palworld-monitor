"""End-to-end smoke tests for the Palworld Monitor web UI.

These tests start the full application and exercise it through a real browser.
They are marked with `e2e` and excluded from the normal unit-test run by default.
Run them with: uv run poe smoke
"""

import os
import time
from pathlib import Path

import pytest
import platform

from playwright.sync_api import sync_playwright, expect, Page

from src.settings import settings
from .helpers import BASE_URL, kill_existing_palworld

pytestmark = pytest.mark.e2e


def _server_is_on(page: Page) -> bool:
    return page.locator(".status-on").is_visible()


def _server_is_off(page: Page) -> bool:
    return page.locator(".status-off").is_visible()


def _refresh_status(page: Page):
    """Click the Update Server Status button and wait for the UI to update."""
    page.click("#getStatusBtn")
    page.wait_for_timeout(1500)


def _wait_for_status(page: Page, on: bool, timeout: float = 180.0):
    """Poll the dashboard until the server status matches `on`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _refresh_status(page)
        if on and _server_is_on(page):
            return
        if not on and _server_is_off(page):
            return
        time.sleep(2.0)
    raise TimeoutError(
        f"Server did not reach status {'ON' if on else 'OFF'} in {timeout}s"
    )


@pytest.fixture
def page():
    """Launch a browser page for the e2e test."""
    with sync_playwright() as p:
        headless = os.environ.get("SMOKE_HEADLESS", "1") != "0"
        browser = p.chromium.launch(
            headless=headless, slow_mo=150 if not headless else 0
        )
        context = browser.new_context()
        pg = context.new_page()

        # In headless Chromium, native confirm() in onclick handlers blocks
        # even when window.confirm is overridden, and page.on("dialog")
        # accept() doesn't propagate the return value back to JS. Patch
        # script.js to bypass the confirm guard.
        def _bypass_confirm(route):
            body = route.fetch().body().decode()
            body = body.replace(
                "if (confirm('Are you sure you want to stop the server?'))",
                "if (true)",
            )
            route.fulfill(body=body, content_type="application/javascript")

        pg.route("**/script.js", _bypass_confirm)
        pg.on("dialog", lambda dialog: dialog.accept())
        yield pg
        # Navigate to about:blank first to cancel any pending fetch/XHR
        # requests (e.g. a stopServer action stuck in the Flask handler).
        # Otherwise context.close() / browser.close() can hang waiting for
        # the page's event loop to drain.
        try:
            pg.goto("about:blank", timeout=3000)
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass


@pytest.fixture
def logged_in_page(page: Page, app_process):
    """Open the login page, sign in, and return the dashboard page."""
    page.goto(f"{BASE_URL}/login")
    expect(page.locator("text=Admin Login")).to_be_visible()

    page.fill("input[name='username']", settings.webUsername)
    page.fill("input[name='password']", settings.webPassword)
    page.locator("button[type='submit']").click(timeout=10000)
    page.wait_for_url(f"{BASE_URL}/", timeout=15000)

    expect(page.locator("text=Server Status:")).to_be_visible()
    expect(page.locator("text=Players:")).to_be_visible()
    return page


def test_login_and_dashboard_loads(logged_in_page):
    """Smoke test: sign in and verify the dashboard renders."""
    logged_in_page.wait_for_timeout(3500)
    players_info = logged_in_page.locator("#playersInfo")
    expect(players_info).to_be_visible()

    text = players_info.inner_text()
    assert "Player" in text or "No players found" in text


@pytest.mark.skipif(
    platform.system() == "Linux", reason="timing-unstable on GitHub Ubuntu runners"
)
def test_server_lifecycle(logged_in_page):
    """Start the Palworld server, read status, then stop it through the web UI."""
    kill_existing_palworld()

    # Make sure the UI shows the server as OFF first.
    _refresh_status(logged_in_page)
    if _server_is_on(logged_in_page):
        logged_in_page.locator("#offBtn").click(timeout=10000)
        logged_in_page.wait_for_selector(
            ".status-off:not([style*='display: none'])", timeout=30000
        )

    logged_in_page.locator("#onBtn").click(timeout=10000)
    _wait_for_status(logged_in_page, on=True, timeout=90.0)

    # Read status and player info via the REST-backed dashboard.
    time.sleep(10)
    _refresh_status(logged_in_page)
    players_info = logged_in_page.locator("#playersInfo")
    expect(players_info).to_be_visible()

    # Stop the server.  Short timeout — the SSE /stream connection
    # keeps the page from ever reaching "stable".
    logged_in_page.locator("#offBtn").click(timeout=10000)
    logged_in_page.wait_for_selector(
        ".status-off:not([style*='display: none'])", timeout=60000
    )
    time.sleep(6)  # let cooldown expire


@pytest.mark.skipif(
    platform.system() == "Linux", reason="timing-unstable on GitHub Ubuntu runners"
)
def test_player_management(logged_in_page):
    """Verify player list from the dummy API, then kick and ban a player."""
    kill_existing_palworld()

    _refresh_status(logged_in_page)
    if _server_is_on(logged_in_page):
        logged_in_page.locator("#offBtn").click(timeout=10000)
        logged_in_page.wait_for_selector(
            ".status-off:not([style*='display: none'])", timeout=30000
        )

    logged_in_page.locator("#onBtn").click(timeout=10000)
    _wait_for_status(logged_in_page, on=True, timeout=60.0)

    # Let the polling loop pick up players from the dummy API.
    time.sleep(8)
    _refresh_status(logged_in_page)
    expect(logged_in_page.locator("#playersInfo")).to_be_visible()
    assert logged_in_page.locator(".status-online").count() == 3

    # Kick TestPlayer1.
    logged_in_page.locator("text=TestPlayer1").locator("..").locator(".kick-btn").click(
        timeout=5000
    )
    # Wait for the next poll cycle to pick up the change.
    time.sleep(8)
    _refresh_status(logged_in_page)
    kick_count = logged_in_page.locator(".status-online").count()
    assert kick_count == 2, f"Expected 2 online after kick, got {kick_count}"

    # Ban TestPlayer2.
    other = logged_in_page.context.new_page()
    other.goto(BASE_URL)
    logged_in_page.locator("text=TestPlayer2").locator("..").locator(".ban-btn").click(
        timeout=5000
    )
    try:
        for dashboard in (logged_in_page, other):
            row = dashboard.locator("tr").filter(has_text="TestPlayer2")
            expect(row.locator(".status-banned")).to_have_text("Banned")
            expect(row.locator(".unban-btn")).to_be_visible()

        logged_in_page.reload()
        row = logged_in_page.locator("tr").filter(has_text="TestPlayer2")
        expect(row.locator(".unban-btn")).to_be_visible()
        row.locator(".unban-btn").click()
        for dashboard in (logged_in_page, other):
            row = dashboard.locator("tr").filter(has_text="TestPlayer2")
            expect(row.locator(".unban-btn")).to_have_count(0)
            expect(row.locator(".ban-btn")).to_be_visible()

        logged_in_page.reload()
        expect(logged_in_page.locator(".status-banned")).to_have_count(0)
    finally:
        other.close()

    logged_in_page.locator("#offBtn").click(timeout=5000)
    logged_in_page.wait_for_selector(
        ".status-off:not([style*='display: none'])", timeout=60000
    )


def test_ban_state_overrides_stale_online_roster(page):
    page.set_content('<div id="playersInfo"></div>')
    page.add_script_tag(
        path=str(Path(__file__).resolve().parents[2] / "src/static/script.js")
    )
    source = page.evaluate("""() => {
        const response = {
            players: [{steam_id: 'steam_123', name: 'Test', level: '1', currently_online: true}],
            banned_players: ['steam_123']
        };
        updatePlayerInfoUI({}, response);
        return response.players[0];
    }""")
    expect(page.locator(".status-banned")).to_have_text("Banned")
    expect(page.locator(".unban-btn")).to_be_visible()
    expect(page.locator(".ban-btn, .kick-btn")).to_have_count(0)
    assert "banned" not in source


@pytest.mark.parametrize("action", ["Kick", "Ban", "Unban"])
def test_moderation_requires_boolean_success(page, action):
    page.set_content('<div id="playersInfo"></div>')
    page.add_script_tag(
        path=str(Path(__file__).resolve().parents[2] / "src/static/script.js")
    )
    page.evaluate("""() => {
        window.confirm = () => true;
        window.alert = message => window.qaAlert = message;
        window.fetch = async () => ({status: 200, ok: true,
            json: async () => ({success: {}, message: 'Request failed', data: {}, players: [], banned_players: []})});
    }""")
    page.evaluate(
        "async action => await window['handle' + action + 'Player']('steam_123', 'Test')",
        action,
    )
    assert page.evaluate("window.qaAlert").startswith("Failed to ")


def test_ban_button_handles_quotes_in_player_name(page):
    page.set_content('<div id="playersInfo"></div>')
    page.add_script_tag(
        path=str(Path(__file__).resolve().parents[2] / "src/static/script.js")
    )
    page.evaluate("""() => {
        window.confirm = () => true;
        window.alert = message => window.qaAlert = message;
        window.fetch = async (url, options) => {
            window.qaTarget = options.body.get('steam_id');
            return {status: 200, ok: true, json: async () => ({success: true, data: {}, players: [], banned_players: []})};
        };
        updatePlayerInfoUI({}, {players: [{steam_id: 'steam_123', name: `O'Neil "QA" <Admin>`, level: '1', currently_online: false}], banned_players: []});
    }""")
    page.locator(".ban-btn").click()
    page.wait_for_function("window.qaAlert !== undefined")
    assert page.evaluate("window.qaTarget") == "steam_123"
