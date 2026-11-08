"""
Factory functions for creating mock objects used across tests.
"""

from unittest.mock import MagicMock


def create_mock_http_response(
    status_code=200, content=b'{"success": true}', json_data=None, raise_for_status=None
):
    """Create a mock HTTP response object.

    Args:
        status_code: HTTP status code (default: 200)
        content: Response content bytes (default: b'{"success": true}')
        json_data: JSON data to return from json() method (default: {"success": True})
        raise_for_status: Exception to raise from raise_for_status() (default: None)

    Returns:
        MagicMock configured as an HTTP response
    """
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.json.return_value = json_data or {"success": True}
    response.raise_for_status.return_value = raise_for_status
    return response
