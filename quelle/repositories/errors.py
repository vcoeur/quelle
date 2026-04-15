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
    """HTTP / DNS / timeout / upstream API failure."""


class RateLimitError(NetworkError):
    """Upstream API returned 429 or an equivalent quota signal."""


class CacheError(PublicationsError):
    """Local cache / SQLite failure."""


class ConfigError(PublicationsError):
    """Missing or invalid configuration."""
