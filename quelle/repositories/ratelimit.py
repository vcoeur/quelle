"""Shared minimum-interval rate limiter for the source modules.

arXiv (3 s), Unpaywall (100 ms), and Google Books (100 ms) all ask
clients to space out requests. Each source module holds one module-level
`RateLimiter` instance, so the interval is enforced process-wide per
source. The limiter is thread-safe; it does nothing across processes
(a shell loop of `quelle fetch` invocations is not throttled).
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Enforce a minimum interval between successive `wait()` calls.

    :param min_interval_seconds: minimum spacing between calls; `wait()`
        sleeps for the remainder when called back-to-back.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._last_call_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Sleep just long enough to respect the minimum interval."""
        with self._lock:
            elapsed = time.monotonic() - self._last_call_at
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            self._last_call_at = time.monotonic()

    def reset_for_tests(self) -> None:
        """Test hook — clears the last-call timestamp so tests don't pay the interval."""
        with self._lock:
            self._last_call_at = 0.0

    def set_last_call_for_tests(self, value: float) -> None:
        """Test hook — pin the last-call timestamp."""
        with self._lock:
            self._last_call_at = value
