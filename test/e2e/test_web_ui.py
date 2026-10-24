"""End-to-end smoke tests for the Palworld Monitor web UI.

These tests start the full application and exercise it through a real browser.
They are marked with `e2e` and excluded from the normal unit-test run by default.
Run them with: uv run poe smoke
"""

import os
import time

import pytest

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
    _refresh_status(logged_in_page)
    players_info = logged_in_page.locator("#playersInfo")
    expect(players_info).to_be_visible()
    expect(logged_in_page.locator(".status-online")).to_have_count(3, timeout=30000)

    # Stop the server.  Short timeout — the SSE /stream connection
    # keeps the page from ever reaching "stable".
    logged_in_page.locator("#offBtn").click(timeout=10000)
    logged_in_page.wait_for_selector(
        ".status-off:not([style*='display: none'])", timeout=60000
    )
    time.sleep(6)  # let cooldown expire


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
    _refresh_status(logged_in_page)
    expect(logged_in_page.locator("#playersInfo")).to_be_visible()
    expect(logged_in_page.locator(".status-online")).to_have_count(3, timeout=30000)

    # Kick TestPlayer1.
    logged_in_page.locator("text=TestPlayer1").locator("..").locator(".kick-btn").click(
        timeout=5000
    )
    # Wait for the next poll cycle to pick up the change.
    expect(logged_in_page.locator(".status-online")).to_have_count(2, timeout=30000)

    # Ban TestPlayer2.
    logged_in_page.locator("text=TestPlayer2").locator("..").locator(".ban-btn").click(
        timeout=5000
    )
    expect(logged_in_page.locator(".status-online")).to_have_count(1, timeout=30000)

    logged_in_page.locator("#offBtn").click(timeout=5000)
    logged_in_page.wait_for_selector(
        ".status-off:not([style*='display: none'])", timeout=60000
    )
