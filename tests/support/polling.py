"""Bounded asynchronous polling for integration and E2E assertions."""

import time
from collections.abc import Callable


def poll_until[T](
    predicate: Callable[[], T],
    *,
    timeout: float,
    interval: float = 0.1,
) -> T:
    """Call ``predicate`` until it returns a truthy value or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"Condition was not met within {timeout}s")
