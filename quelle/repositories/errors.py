"""Error hierarchy — the CLI maps these to exit codes.

0 success
1 user error / not found
2 network error / rate limit
3 cache / local store error
4 config error
"""

from __future__ import annotations


class PublicationsError(Exception):
    """Base class for all expected errors."""


class UserError(PublicationsError):
    """Invalid user input."""


class NotFoundError(PublicationsError):
    """No publication found for the given query."""


class NetworkError(PublicationsError):
    """HTTP / DNS / timeout / upstream API failure.

    Carries the upstream HTTP status code when one is known
    (`status_code is None` for transport-level failures such as DNS
    errors and timeouts), so callers can branch on the status —
    e.g. map an upstream 404 to `NotFoundError` — without parsing
    the message text.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(NetworkError):
    """Upstream API returned 429 or an equivalent quota signal."""


class CacheError(PublicationsError):
    """Local cache / SQLite failure."""


class ConfigError(PublicationsError):
    """Missing or invalid configuration."""
