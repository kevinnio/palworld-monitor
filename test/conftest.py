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
    """Automatically clean up PID files and event bus subscribers after each test."""
    yield
    # Reset event bus subscribers so stale handlers from earlier tests don't
    # fire (and start background threads) during later tests.
    from src.events import bus

    bus._subscribers.clear()
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
    from unittest.mock import MagicMock

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
    mock.sessionSecretKey = (
        "test_secret_key_123456789012345678901234567890"
    )
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
    monkeypatch.setattr("src.palworld_control.settings", mock)
    monkeypatch.setattr("src.auto_start.settings", mock)
    monkeypatch.setattr("src.player_manager.settings", mock)
    monkeypatch.setattr("src.banlist_manager.settings", mock)
    monkeypatch.setattr("src.web_server.settings", mock)
    return mock


@pytest.fixture
def mock_process_manager():
    """Create a mock process manager for testing."""
    from unittest.mock import MagicMock

    pm = MagicMock()
    pm.is_process_running.return_value = False
    pm.launched_pid = None
    pm.launch_process.return_value = None
    pm.terminate_process.return_value = True
    pm.find_process_pid.return_value = None
    pm.pid_file_name.return_value = "test.pid"
    pm.set_known_pid.return_value = None
    return pm


@pytest.fixture
def mock_player_manager():
    """Create a mock player manager for testing."""
    from unittest.mock import MagicMock

    pm = MagicMock()
    pm.players = {}
    pm.get_all_players.return_value = []
    pm.get_online_players.return_value = []
    pm.get_offline_players.return_value = []
    pm.get_player_count.return_value = 0
    pm.get_total_player_count.return_value = 0
    pm.update_players_from_server.return_value = None
    return pm


@pytest.fixture
def mock_banlist_manager():
    """Create a mock banlist manager for testing."""
    from unittest.mock import MagicMock

    bm = MagicMock()
    bm.get_banned_players.return_value = []
    bm.is_banned.return_value = False
    bm.add_ban.return_value = True
    bm.remove_ban.return_value = True
    return bm


@pytest.fixture
def client(web_app_factory, mock_settings):
    """Create an unauthenticated Flask test client with proper credentials configured."""
    return web_app_factory.app.test_client()


@pytest.fixture
def web_app_factory():
    from src.web_server import WebServer

    controller = MagicMock()
    controller.is_palworld_process_running.return_value = False
    controller.get_players_for_web.return_value = []
    controller.get_banned_players.return_value = []
    controller.player_manager.get_online_players.return_value = []
    controller.start_server.return_value = True
    controller.stop_server.return_value = True
    controller.kick_player.return_value = True
    controller.ban_player.return_value = True
    controller.unban_player.return_value = True

    server = WebServer(controller)
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

    Yields:
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


@pytest.fixture
def short_lockout_client(web_app_factory):
    """Create a Flask test client with a very short lockout duration for testing.

    This fixture creates a new WebServer instance with lockout_duration=1 second,
    allowing lockout tests to complete quickly without waiting.

    Yields:
        Flask test client with active session and 1-second lockout
    """
    from src.web_server import WebServer

    # Create a new app instance with short lockout
    controller = MagicMock()
    controller.is_palworld_process_running.return_value = False
    controller.get_players_for_web.return_value = []
    controller.player_manager.get_online_players.return_value = []
    controller.start_server.return_value = True
    controller.stop_server.return_value = True
    controller.kick_player.return_value = True
    controller.ban_player.return_value = True
    controller.unban_player.return_value = True

    server = WebServer.__new__(WebServer)
    server.palworld_controller = controller
    server.state_cache = {
        "running": False,
        "playerCount": 0,
        "players": [],
        "banned_players": [],
    }
    server._lock = MagicMock()

    # Subscribe to events (required for proper state management)
    from src.events import bus, Event

    bus.subscribe(Event.SERVER_STARTED, server._on_server_started)
    bus.subscribe(Event.SERVER_STOPPED, server._on_server_stopped)
    bus.subscribe(Event.SERVER_STATUS, server._on_server_status)

    # Sync initial state
    server._sync_running_state()
    server._sync_banned_players()

    yield server.app.test_client()
