"""
Pytest configuration and shared fixtures for the entire test suite.
"""

import sys
import os
import pytest
import tempfile
import shutil
from unittest.mock import MagicMock

# Add project root and src directory to path (once, at module level)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "src"))

# Fix Windows console encoding for emoji support
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


@pytest.fixture
def test_sleep_script():
    """Path to the dummy PalServer sleep script."""
    return os.path.join(os.path.dirname(__file__), "e2e", "dummy", "PalServer-Dummy.py")


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture(autouse=True)
def cleanup_pid_files():
    """Remove PID files written by process-manager tests."""
    yield
    pid_files = [
        "palworld_server.win.pid",
        "palworld_server.linux.pid",
        "palworld_server.lgsm.pid",
    ]
    for pid_file in pid_files:
        if os.path.exists(pid_file):
            try:
                os.remove(pid_file)
            except Exception:
                pass


@pytest.fixture
def mock_settings(monkeypatch):
    """Shared settings fixture with all required attributes."""
    mock = MagicMock()
    mock.palworldServerHost = "localhost"
    mock.palworldServerPort = 8211
    mock.palworldRESTPort = 8212
    mock.palworldServerAdminPassword = "test_admin_password"
    mock.palworldServerExePath = "/path/to/PalServer.exe"
    mock.palworldMainProcessName = "PalServer-Win64-Shipping-Cmd.exe"
    mock.palworldExeArguments = "-test-args"
    mock.useWebServer = True
    mock.webServerPort = 8213
    mock.webUsername = "test_admin"
    mock.webPassword = "test_web_password"
    mock.sessionSecretKey = "test_secret_key_123456789012345678901234567890"
    mock.sessionTimeout = 3600
    mock.maxLoginAttempts = 5
    mock.lockoutDuration = 300
    mock.rateLimitEnabled = True
    mock.rateLimitRequests = 100
    mock.rateLimitWindow = 60
    mock.autoStart = True
    mock.autoStop = True
    mock.autoStopDelay = 120
    mock.enablePlayerTracking = True
    mock.pollingRate = 5
    mock.protocol = "REST"
    mock.controlServerThroughWeb = True
    mock.showServerIPAddress = False
    mock.firstPacketPattern = b"\x09\x08\x00"

    # Patch settings import in all modules that use it
    monkeypatch.setattr("src.api_clients.settings", mock)
    monkeypatch.setattr("src.lifecycle.supervisor.settings", mock)
    monkeypatch.setattr("src.lifecycle.runtime.settings", mock)
    monkeypatch.setattr("src.auto_start.settings", mock)
    monkeypatch.setattr("src.player_manager.settings", mock)
    monkeypatch.setattr("src.banlist_manager.settings", mock)
    monkeypatch.setattr("src.process_manager.settings", mock)
    monkeypatch.setattr("src.web_server.settings", mock)
    return mock


@pytest.fixture
def client(web_app_factory, mock_settings):
    """Create an unauthenticated Flask test client with proper credentials configured."""
    return web_app_factory.app.test_client()


@pytest.fixture
def web_app_factory():
    from src.events import EventBus
    from src.web_server import WebServer

    controller = MagicMock()
    controller.get_snapshot.return_value = {
        "revision": 0,
        "state": "STOPPED",
        "playerData": "unknown",
        "playerCount": 0,
        "players": [],
        "banned_players": [],
    }
    controller.start_server.return_value = True
    controller.stop_server.return_value = True
    controller.kick_player.return_value = True
    controller.ban_player.return_value = True
    controller.unban_player.return_value = True

    server = WebServer(controller, bus=EventBus())
    # Ensure Flask app has a secret key for CSRF token generation in templates
    server.app.secret_key = "test_secret_key_123456789012345678901234567890"
    server.app.config["WTF_CSRF_ENABLED"] = False  # disable CSRF validation in tests

    return server


@pytest.fixture
def auth_client(web_app_factory, mock_settings):
    """Create a Flask test client pre-authenticated as the admin user.

    This fixture logs in with valid credentials and returns a client that can access
    protected routes without needing to POST login first.

    Args:
        web_app_factory: Real WebServer instance with Flask app
        mock_settings: Test credentials (username=test_admin, password=test_web_password)

    Returns:
        Flask test client with active session
    """
    # Create a test client and login as admin user
    auth_client = web_app_factory.app.test_client()
    response = auth_client.post(
        "/login",
        data={
            "username": mock_settings.webUsername,
            "password": mock_settings.webPassword,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302, (
        f"Expected redirect after login, got {response.status_code}"
    )

    # Return the authenticated client
    return auth_client
