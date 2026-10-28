"""
Tests for the API clients module (RestClient and RconClient).
"""

import json

import pytest
from unittest.mock import patch, MagicMock
import requests
from src.api_clients import RestClient


@pytest.fixture
def mock_http_response():
    """Create a mock HTTP response with default values."""
    from test.support import create_mock_http_response

    return create_mock_http_response()


class TestRestClient:
    """Test suite for RestClient."""

    def test_init(self, mock_settings, mock_http_response):
        """Test RestClient can be initialized and make requests."""
        client = RestClient()
        # Test behavior: client can successfully make requests
        mock_http_response.content = b'{"success": true}'
        mock_http_response.json.return_value = {"success": True}

        with patch("requests.get", return_value=mock_http_response):
            result = client._make_get_request("test")
            assert result == {"success": True}

    def test_make_request_success(self, mock_settings, mock_http_response):
        """Test successful HTTP request returns expected data."""
        mock_http_response.content = b'{"success": true, "data": "test"}'
        mock_http_response.json.return_value = {"success": True, "data": "test"}

        with patch("requests.get", return_value=mock_http_response):
            client = RestClient()
            result = client._make_get_request("test")

            # Test behavior: request returns the expected data
            assert result == {"success": True, "data": "test"}

    @pytest.mark.parametrize(
        "status_code,expected",
        [(200, True), (204, False), (400, False), (401, False), (500, False)],
    )
    def test_make_request_no_content(self, mock_settings, status_code, expected):
        """Moderation returns a boolean even when PalServer sends no body."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.content = b""
        mock_response.raise_for_status.return_value = None

        with patch("requests.post", return_value=mock_response):
            client = RestClient()
            result = client._make_post_request("test")

            assert result is expected

    def test_make_request_connection_error(self, mock_settings):
        """Test failed HTTP request with connection error."""
        with patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("Connection failed"),
        ):
            client = RestClient()
            result = client._make_get_request("test")

            assert result is None

    def test_make_request_timeout(self, mock_settings):
        """Test HTTP request timeout."""
        with patch(
            "requests.get", side_effect=requests.exceptions.Timeout("Request timeout")
        ):
            client = RestClient()
            result = client._make_get_request("test")

            assert result is None

    def test_make_request_http_error(self, mock_settings):
        """Test HTTP request with HTTP error status."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "Server error"
        )

        with patch("requests.get", return_value=mock_response):
            client = RestClient()
            result = client._make_get_request("test")

            assert result is None

    @pytest.mark.parametrize(
        "players_data,expected_count",
        [
            ([{"name": "P1"}, {"name": "P2"}], 2),
            ([], 0),
        ],
    )
    def test_get_player_count(
        self, mock_settings, mock_http_response, players_data, expected_count
    ):
        """Test getting player count with various scenarios."""
        mock_http_response.content = json.dumps({"players": players_data}).encode()
        mock_http_response.json.return_value = {"players": players_data}

        with patch("requests.get", return_value=mock_http_response):
            client = RestClient()
            count = client.get_player_count()

            assert count == expected_count

    def test_get_player_count_request_fails(self, mock_settings):
        """Test getting player count when request fails."""
        with patch(
            "requests.get", side_effect=requests.exceptions.RequestException("Error")
        ):
            client = RestClient()
            count = client.get_player_count()

            assert count is None

    def test_get_player_names(self, mock_settings, mock_http_response):
        """Test getting player names."""
        player_data = [{"name": "P1", "userId": "u1", "playerId": "p1", "level": "5"}]
        mock_http_response.content = json.dumps(player_data).encode()
        mock_http_response.json.return_value = player_data

        with patch("requests.get", return_value=mock_http_response):
            client = RestClient()
            players = client.get_player_names()

            assert len(players) == 1
            assert players[0] == ["P1", "p1", "u1", "5"]

    def test_get_player_names_empty_success_is_an_empty_roster(
        self, mock_settings, mock_http_response
    ):
        mock_http_response.content = json.dumps({"players": []}).encode()
        mock_http_response.json.return_value = {"players": []}

        with patch("requests.get", return_value=mock_http_response):
            assert RestClient().get_player_names() == []

    def test_get_player_names_failure_is_unavailable(self, mock_settings):
        """A failed REST call must not look like a successful empty roster."""
        with patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("Connection failed"),
        ):
            assert RestClient().get_player_names() is None

    def test_get_player_names_http_error_is_unavailable(
        self, mock_settings, mock_http_response
    ):
        mock_http_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "Server error"
        )

        with patch("requests.get", return_value=mock_http_response):
            assert RestClient().get_player_names() is None

    @pytest.mark.parametrize(
        "status_code,expected_result",
        [
            (200, True),
            (400, False),
            (500, False),
        ],
    )
    def test_kick_player(self, mock_settings, status_code, expected_result):
        """Test kicking a player with success and failure scenarios."""
        mock_response = MagicMock()
        mock_response.status_code = status_code

        with patch("requests.post", return_value=mock_response) as mock_post:
            client = RestClient()
            player = {"steam_id": "123456789", "name": "TestPlayer"}
            result = client.kick_player(player)

            assert result == expected_result
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            assert kwargs["json"] == {"userid": "123456789"}

    @pytest.mark.parametrize(
        "status_code,expected_result",
        [
            (200, True),
            (400, False),
            (500, False),
        ],
    )
    def test_ban_player(self, mock_settings, status_code, expected_result):
        """Test banning a player via REST API."""
        mock_response = MagicMock()
        mock_response.status_code = status_code

        with patch("requests.post", return_value=mock_response) as mock_post:
            client = RestClient()
            player = {"steam_id": "123456789", "name": "TestPlayer"}
            result = client.ban_player(player)

            assert result == expected_result
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            assert kwargs["json"] == {"userid": "123456789"}

    @pytest.mark.parametrize(
        "status_code,expected_result",
        [
            (200, True),
            (400, False),
            (500, False),
        ],
    )
    def test_unban_player(self, mock_settings, status_code, expected_result):
        """Test unbanning a player via REST API."""
        mock_response = MagicMock()
        mock_response.status_code = status_code

        with patch("requests.post", return_value=mock_response) as mock_post:
            client = RestClient()
            player = {"steam_id": "123456789", "name": "TestPlayer"}
            result = client.unban_player(player)

            assert result == expected_result
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            assert kwargs["json"] == {"userid": "123456789"}
