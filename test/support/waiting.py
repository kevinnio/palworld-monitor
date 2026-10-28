"""Bounded polling for asynchronous test results."""

import time


def wait_until(predicate, timeout=5, message="condition not met"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return

        time.sleep(0.01)

    assert predicate(), message
