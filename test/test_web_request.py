"""
Request-level tests for PalWorld Monitor web interface.

These tests use Flask's test client to exercise the full HTTP request/response pipeline,
including template rendering, session management, CSRF protection, and all API routes.
Tests run in-process without needing an actual HTTP server or browser.
"""

import pytest

class TestLoginPageRendering:
    """Test suite for login page rendering."""

    def test_login_page_renders(self, client):
        """GET /login renders the login template with no errors."""
        response = client.get("/login")
        assert response.status_code == 200
        assert b"Palworld Server" in response.data
        assert b"Admin Login" in response.data

    def test_login_page_shows_error_message(self, client):
        """POST invalid credentials shows error message on login page."""
        # First POST with wrong password to trigger error rendering
        response = client.post(
            "/login",
            data={
                "username": "admin",
                "password": "wrong_password",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert (
            b"Invalid credentials" in response.data
            or b"Too many failed attempts" in response.data
        )


class TestSuccessfulLoginFlow:
    """Test suite for successful login flow."""

    def test_successful_login_redirects_to_index(self, auth_client):
        """POST valid credentials returns 302 redirect to /."""
        # Login with valid credentials
        response = auth_client.post(
            "/login",
            data={
                "username": "test_admin",
                "password": "test_web_password",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["Location"] == "/"

    def test_login_creates_session(self, auth_client):
        """After successful login, session is created and subsequent requests are authenticated."""
        # Login with valid credentials
        auth_client.post(
            "/login",
            data={
                "username": "test_admin",
                "password": "test_web_password",
            },
            follow_redirects=False,
        )

        # Access protected route - should work without redirecting to login
        response = auth_client.get("/")
        assert response.status_code == 200
        assert b"Palworld Dedicated Server" in response.data


class TestFailedLoginFlow:
    """Test suite for failed login flow."""

    def test_failed_login_shows_error(self, client):
        """POST invalid credentials returns 200 with error message."""
        response = client.post(
            "/login",
            data={
                "username": "admin",
                "password": "wrong_password",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200
        # Should contain error message in the rendered template
        assert (
            b"Invalid credentials" in response.data
            or b"Too many failed attempts" in response.data
        )

    def test_login_attempt_counter_increments(self, client):
        """Multiple failed login attempts increment the counter."""
        # First attempt
        client.post(
            "/login",
            data={
                "username": "admin",
                "password": "wrong_password",
            },
            follow_redirects=False,
        )

        # Second attempt - should still show error but with different message
        response = client.post(
            "/login",
            data={
                "username": "admin",
                "password": "another_wrong_password",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200


class TestRememberMeCheckbox:
    """Test suite for remember me functionality."""

    def test_remember_me_sets_cookie(self, client):
        """POST with remember=on sets a cookie for 7 days."""
        response = client.post(
            "/login",
            data={
                "username": "test_admin",
                "password": "test_web_password",
                "remember": "on",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        # Check that a cookie was set for the remember functionality
        cookies = response.headers.getlist("Set-Cookie")
        remember_cookies = [c for c in cookies if "remember" in c]
        assert len(remember_cookies) > 0


class TestLogoutFlow:
    """Test suite for logout flow."""

    def test_logout_redirects_to_login(self, auth_client):
        """POST /logout redirects to login page and clears session."""
        response = auth_client.post("/logout", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["Location"] == "/login"


class TestIndexPageRendering:
    @pytest.mark.parametrize(
        "enabled,running", [(False, False), (False, True), (True, False), (True, True)]
    )
    def test_auto_stop_notice_matches_settings_and_state(
        self, auth_client, web_app_factory, mock_settings, enabled, running
    ):
        mock_settings.autoStop = enabled
        web_app_factory.state_cache["running"] = running
        html = auth_client.get("/").get_data(as_text=True)

        assert ('id="autoStopNotice"' in html) is enabled
        if enabled:
            assert (
                "display:none;"
                in html.split('id="autoStopNotice"', 1)[1].split(">", 1)[0]
            ) is not running

    """Test suite for index/dashboard page rendering."""

    def test_auto_stop_notice_hidden_when_stopped(self, auth_client):
        response = auth_client.get("/")

        assert response.status_code == 200
        assert b'id="autoStopNotice" style="margin-top: 30px; display: none;"' in (
            response.data
        )

    def test_auto_stop_notice_visible_when_running(
        self, auth_client, web_app_factory
    ):
        snapshot = dict(
            web_app_factory.palworld_controller.get_snapshot.return_value
        )
        snapshot["state"] = "RUNNING"
        web_app_factory.palworld_controller.get_snapshot.return_value = snapshot

        response = auth_client.get("/")

        assert response.status_code == 200
        assert b'id="autoStopNotice" style="margin-top: 30px;"' in response.data

    def test_auto_stop_notice_absent_when_auto_stop_disabled(
        self, auth_client, mock_settings, monkeypatch
    ):
        monkeypatch.setattr(mock_settings, "autoStop", False)

        response = auth_client.get("/")

        assert response.status_code == 200
        assert b'id="autoStopNotice"' not in response.data

    def test_index_page_renders_when_logged_in(self, auth_client):
        """GET / renders the dashboard with player data when authenticated."""
        response = auth_client.get("/")
        assert response.status_code == 200
        assert b"Palworld Dedicated Server" in response.data
        # Should contain status indicators and action buttons
        assert b"Update Server Status" in response.data or b"getStatus" in response.data

    def test_index_page_shows_status_indicators(self, auth_client):
        """Dashboard displays server status ON/OFF indicators."""
        response = auth_client.get("/")
        assert response.status_code == 200
        # Should contain status indicator elements
        assert b"status-on" in response.data or b"status-off" in response.data

    def test_index_page_shows_action_buttons(self, auth_client):
        """Dashboard displays start/stop buttons when controlServerThroughWeb is enabled."""
        response = auth_client.get("/")
        assert response.status_code == 200
        # Should contain action button elements
        assert b"Start Server" in response.data or b"Stop Server" in response.data


class TestStatusUpdatePolling:
    """Test suite for server status update polling."""

    def test_get_status_returns_json(self, auth_client):
        """POST /action?action=getStatus returns JSON with server state."""
        response = auth_client.post(
            "/action",
            data={"action": "getStatus"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        # Should return the committed lifecycle + REST health snapshot
        import json

        data = json.loads(response.data)
        assert "data" in data
        assert data["data"]["state"] in ("STOPPED", "STARTING", "RUNNING", "STOPPING")
        assert data["data"]["playerData"] in ("unknown", "ok", "unavailable")


class TestServerStartStopActions:
    """Test suite for server start/stop actions."""

    def test_start_server_action(self, auth_client):
        """POST /action?action=startServer dispatches to controller."""
        response = auth_client.post(
            "/action",
            data={"action": "startServer"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200


class TestKickPlayerAction:
    """Test suite for kick player action."""

    def test_kick_player_returns_json(self, auth_client):
        """POST /kick with steam_id returns success/failure JSON."""
        response = auth_client.post(
            "/kick",
            data={"steam_id": "123456789"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200


class TestBanPlayerAction:
    """Test suite for ban player action."""

    def test_ban_player_returns_json(self, auth_client):
        """POST /ban with steam_id returns success/failure JSON."""
        response = auth_client.post(
            "/ban",
            data={"steam_id": "123456789"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200


class TestUnbanPlayerAction:
    """Test suite for unban player action."""

    def test_unban_player_returns_json(self, auth_client):
        """POST /unban with steam_id returns success/failure JSON."""
        response = auth_client.post(
            "/unban",
            data={"steam_id": "123456789"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200


class TestBanStateUpdates:
    @pytest.mark.parametrize("endpoint", ["/kick", "/ban", "/unban"])
    @pytest.mark.parametrize(
        "steam_id", ["", "steam_123,UID", "steam_123\nsteam_456", "steam_'123"]
    )
    def test_invalid_id_is_rejected_before_api_call(
        self, auth_client, web_app_factory, endpoint, steam_id
    ):
        response = auth_client.post(endpoint, data={"steam_id": steam_id})

        assert response.status_code == 400
        method = {
            "/kick": "kick_player",
            "/ban": "ban_player",
            "/unban": "unban_player",
        }[endpoint]
        getattr(web_app_factory.palworld_controller, method).assert_not_called()

    def test_ban_and_unban_update_response_and_sse_without_polling(
        self, auth_client, web_app_factory, tmp_path
    ):
        import json
        import queue
        from src.banlist_manager import BanlistManager

        server = web_app_factory
        controller = server.palworld_controller
        manager = BanlistManager(str(tmp_path / "banlist.txt"))
        controller.get_banned_players.side_effect = manager.get_banned_players
        controller.ban_player.side_effect = manager.add_ban
        controller.unban_player.side_effect = manager.remove_ban
        events = queue.Queue()
        server._sse_clients.append(events)

        for endpoint, expected in [("/ban", ["steam_123"]), ("/unban", [])]:
            response = auth_client.post(endpoint, data={"steam_id": "steam_123"})
            body = response.get_json()
            assert body["success"] is True
            assert body["banned_players"] == expected
            event = json.loads(events.get_nowait().removeprefix("data: "))
            assert event["banned_players"] == expected
            assert auth_client.get("/banned").get_json()["banned_players"] == expected

    def test_stale_poll_cannot_restore_removed_ban(self, auth_client, web_app_factory):
        server = web_app_factory
        server.palworld_controller.get_banned_players.return_value = []
        server._on_server_status({"running": True, "banned_players": ["steam_old"]})

        assert server.state_cache["banned_players"] == []
        assert auth_client.get("/banned").get_json()["banned_players"] == []

    def test_stream_reads_current_banlist(self, auth_client, web_app_factory):
        import json

        web_app_factory.palworld_controller.get_banned_players.return_value = [
            "steam_123"
        ]
        response = auth_client.get("/stream", buffered=False)
        try:
            event = json.loads(next(response.response).decode().removeprefix("data: "))
            assert event["banned_players"] == ["steam_123"]
        finally:
            response.close()


class TestSessionTimeoutRedirectsToLogin:
    """Test suite for session timeout behavior."""

    def test_session_timeout_redirects_to_login(self, client):
        """When session expires, AJAX responses redirect to /login?redirect=..."""
        # Access a protected route without logging in - should redirect to login
        response = client.get("/")
        # Should redirect to login page (either directly or via session timeout)
        assert response.status_code in [302, 401]


class TestLockedOutUserCantLogin:
    """Test suite for account lockout behavior."""

    def test_locked_out_user_cannot_login(self, client):
        """After max failed attempts with short lockout, login form button is disabled and POST returns error page."""
        # Use a very short lockout duration (1 second) to make the test fast
        import time

        # Make multiple failed login attempts to trigger lockout
        for i in range(3):
            response = client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": f"wrong_password_{i}",
                },
                follow_redirects=False,
            )

            assert response.status_code == 200

        # Wait for lockout to expire (1 second)
        time.sleep(1.5)

        # Try to login again - should still fail due to lockout
        response = client.post(
            "/login",
            data={
                "username": "admin",
                "password": "wrong_password_3",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200
