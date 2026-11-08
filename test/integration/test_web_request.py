"""
Request-level integration tests for PalWorld Monitor web interface.

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
        assert b"Invalid credentials" in response.data

    def test_failed_attempts_count_down_then_lock_out(self, web_app_factory):
        """Each failure decrements the allowance, warns, then locks the account."""
        client = web_app_factory.app.test_client()

        def fail_login():
            return client.post(
                "/login",
                data={"username": "admin", "password": "wrong_password"},
                follow_redirects=False,
            ).get_data(as_text=True)

        # mock_settings.maxLoginAttempts is 5; warnings start at two remaining.
        assert "attempts remaining" not in fail_login()
        assert "attempts remaining" not in fail_login()
        assert "2 attempts remaining before lockout" in fail_login()
        assert "1 attempts remaining before lockout" in fail_login()
        assert "Too many failed attempts. Account locked." in fail_login()


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
    """Test suite for index/dashboard page rendering."""

    @pytest.mark.parametrize(
        "enabled,running", [(False, False), (False, True), (True, False), (True, True)]
    )
    def test_auto_stop_notice_matches_settings_and_state(
        self, auth_client, web_app_factory, mock_settings, enabled, running
    ):
        mock_settings.autoStop = enabled
        snapshot = dict(web_app_factory.palworld_controller.get_snapshot.return_value)
        snapshot["state"] = "RUNNING" if running else "STOPPED"
        web_app_factory.palworld_controller.get_snapshot.return_value = snapshot
        html = auth_client.get("/").get_data(as_text=True)

        assert ('id="autoStopNotice"' in html) is enabled
        if enabled:
            assert (
                "display: none;"
                in html.split('id="autoStopNotice"', 1)[1].split(">", 1)[0]
            ) is not running

    def test_index_page_renders_when_logged_in(self, auth_client):
        """GET / renders the dashboard with player data when authenticated."""
        response = auth_client.get("/")
        assert response.status_code == 200
        assert b"Palworld Dedicated Server" in response.data
        # Should contain status indicators and action buttons
        assert b"Update Server Status" in response.data or b"getStatus" in response.data

    @pytest.mark.parametrize(
        "state,visible,hidden",
        [("STOPPED", "onBtn", "offBtn"), ("RUNNING", "offBtn", "onBtn")],
    )
    def test_dashboard_offers_the_action_that_matches_the_state(
        self, auth_client, web_app_factory, state, visible, hidden
    ):
        """The dashboard renders only the start/stop button the state allows."""
        snapshot = dict(web_app_factory.palworld_controller.get_snapshot.return_value)
        snapshot["state"] = state
        web_app_factory.palworld_controller.get_snapshot.return_value = snapshot

        html = auth_client.get("/").get_data(as_text=True)

        assert "display:none" not in self._tag(html, visible)
        assert "display:none" in self._tag(html, hidden)

    def test_dashboard_hides_controls_when_web_control_is_disabled(
        self, auth_client, mock_settings
    ):
        mock_settings.controlServerThroughWeb = False

        html = auth_client.get("/").get_data(as_text=True)

        assert 'id="onBtn"' not in html
        assert 'id="offBtn"' not in html

    @staticmethod
    def _tag(html, element_id):
        start = html.index(f'id="{element_id}"')
        return html[start : html.index(">", start)]


class TestStatusUpdatePolling:
    """Test suite for server status update polling."""

    def test_get_status_returns_the_committed_snapshot(self, auth_client):
        """POST /action returns the same JSON view of the committed state."""
        response = auth_client.post(
            "/action",
            data={"action": "getStatus"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["state"] == "STOPPED"
        assert data["data"]["playerData"] == "unknown"


class TestServerStartStopActions:
    """Test suite for server start/stop actions."""

    @pytest.mark.parametrize(
        "action,method",
        [("startServer", "start_server"), ("stopServer", "stop_server")],
    )
    def test_action_dispatches_to_controller(
        self, auth_client, web_app_factory, mock_settings, action, method
    ):
        """POST /action drives the controller and returns the committed snapshot."""
        response = auth_client.post(
            "/action",
            data={"action": action},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        getattr(web_app_factory.palworld_controller, method).assert_called_once()
        body = response.get_json()
        assert body["data"]["state"] == "STOPPED"
        assert body["autoStopDelay"] == round(mock_settings.autoStopDelay)


class TestKickPlayerAction:
    """Test suite for kick player action."""

    def test_kick_player_returns_json(self, auth_client, web_app_factory):
        """POST /kick routes the Steam ID to the controller."""
        response = auth_client.post(
            "/kick",
            data={"steam_id": "123456789"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        assert response.get_json()["success"] is True
        web_app_factory.palworld_controller.kick_player.assert_called_once_with(
            "123456789"
        )


class TestBanPlayerAction:
    """Test suite for ban player action."""

    def test_ban_player_returns_json(self, auth_client, web_app_factory):
        """POST /ban routes the Steam ID to the controller."""
        response = auth_client.post(
            "/ban",
            data={"steam_id": "123456789"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        assert response.get_json()["success"] is True
        web_app_factory.palworld_controller.ban_player.assert_called_once_with(
            "123456789"
        )


class TestUnbanPlayerAction:
    """Test suite for unban player action."""

    def test_unban_player_returns_json(self, auth_client, web_app_factory):
        """POST /unban routes the Steam ID to the controller."""
        response = auth_client.post(
            "/unban",
            data={"steam_id": "123456789"},
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        assert response.get_json()["success"] is True
        web_app_factory.palworld_controller.unban_player.assert_called_once_with(
            "123456789"
        )


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
        manager = BanlistManager(server.bus, str(tmp_path / "banlist.txt"))
        controller.ban_player.side_effect = manager.add_ban
        controller.unban_player.side_effect = manager.remove_ban
        snapshot = dict(controller.get_snapshot.return_value)
        controller.get_snapshot.side_effect = lambda: {
            **snapshot,
            "banned_players": manager.get_banned_players(),
        }
        events = queue.Queue()
        server._sse_clients.append(events)

        for endpoint, expected in [("/ban", ["steam_123"]), ("/unban", [])]:
            response = auth_client.post(endpoint, data={"steam_id": "steam_123"})
            body = response.get_json()
            assert body["success"] is True
            assert body["data"]["banned_players"] == expected
            event = json.loads(events.get_nowait().removeprefix("data: "))
            assert event["data"]["banned_players"] == expected
            assert auth_client.get("/banned").get_json()["banned_players"] == expected

    def test_stale_poll_cannot_restore_removed_ban(self, auth_client, web_app_factory):
        server = web_app_factory
        assert (
            server.palworld_controller.get_snapshot.return_value["banned_players"] == []
        )

        server._on_committed({"banned_players": ["steam_old"]})

        assert server.snapshot["banned_players"] == []
        assert auth_client.get("/banned").get_json()["banned_players"] == []

    def test_stream_reads_current_banlist(self, auth_client, web_app_factory):
        import json

        controller = web_app_factory.palworld_controller
        controller.get_snapshot.return_value = {
            **controller.get_snapshot.return_value,
            "banned_players": ["steam_123"],
        }
        response = auth_client.get("/stream", buffered=False)
        try:
            event = json.loads(next(response.response).decode().removeprefix("data: "))
            assert event["data"]["banned_players"] == ["steam_123"]
        finally:
            response.close()


class TestSessionTimeoutRedirectsToLogin:
    """Test suite for session timeout behavior."""

    def test_session_timeout_redirects_to_login(self, client):
        """An unauthenticated request is sent to the login page."""
        response = client.get("/")

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


class TestLockedOutUserCantLogin:
    """Test suite for account lockout behavior."""

    def test_locked_out_user_cannot_login(self, web_app_factory):
        """After max failed attempts the login page is replaced by a lockout notice.

        The countdown and per-attempt behaviour is covered in
        TestFailedLoginFlow; this pins the locked-out page itself.
        """
        client = web_app_factory.app.test_client()
        for attempt in range(5):
            response = client.post(
                "/login",
                data={"username": "admin", "password": f"wrong_password_{attempt}"},
                follow_redirects=False,
            )
            assert response.status_code == 200

        locked = client.post(
            "/login",
            data={"username": "test_admin", "password": "test_web_password"},
            follow_redirects=False,
        )

        html = locked.get_data(as_text=True)
        assert "Too many failed attempts. Try again in" in html
        assert "Locked Out" in html
        assert client.get("/").status_code == 302
