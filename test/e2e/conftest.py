"""Shared fixtures for e2e smoke tests."""

import pytest

from .helpers import run_monitor_app


@pytest.fixture(scope="module")
def app_process():
    """Start the monitor app and tear it down after the test session."""
    with run_monitor_app() as process:
        yield process
