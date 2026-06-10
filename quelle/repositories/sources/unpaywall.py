"""Unpaywall client — DOI-based open-access PDF lookup.

Free API, requires an email address as a query parameter (which is
also used as the identifier for reporting abuse). 100k requests /
day, 100 ms recommended between requests. Docs:
https://unpaywall.org/products/api/v2

The 100 ms inter-call interval is enforced via a module-level
`RateLimiter` so a script that fetches many DOIs in a tight loop
stays inside the recommended budget without each caller having to
sleep manually.
"""

from __future__ import annotations

import sys
from typing import Any
from urllib.parse import quote

import httpx

from quelle.repositories.errors import ConfigError, NetworkError, NotFoundError, RateLimitError
from quelle.repositories.http_client import get_json
from quelle.repositories.ratelimit import RateLimiter
from quelle.settings import Settings

API_URL = "https://api.unpaywall.org/v2/{doi}"

_RATE_LIMITER = RateLimiter(min_interval_seconds=0.1)


def lookup_by_doi(client: httpx.Client, settings: Settings, doi: str) -> dict[str, Any]:
    """Return the raw Unpaywall payload for a DOI.

    Raises `ConfigError` when no Unpaywall email is configured and
    propagates `RateLimitError` so callers can back off. A 404 (no
    Unpaywall record for the DOI) returns an empty dict; any other
    network failure degrades to an empty dict with a one-line warning
    on stderr — Unpaywall is enrichment, not a required source.
    """
    email = settings.unpaywall_email or settings.contact_email
    if not email:
        raise ConfigError(
            "Unpaywall requires an email — set UNPAYWALL_EMAIL or QUELLE_CONTACT_EMAIL"
        )
    url = API_URL.format(doi=quote(doi, safe="/"))
    _RATE_LIMITER.wait()
    try:
        return get_json(client, url, params={"email": email})
    except RateLimitError:
        raise
    except NotFoundError:
        return {}
    except NetworkError as exc:
        if exc.status_code == 404:
            return {}
        print(f"warning: Unpaywall lookup failed for {doi}: {exc}", file=sys.stderr)
        return {}


def _reset_rate_limit_for_tests() -> None:
    """Test hook — clears the last-call timestamp so tests don't pay 100 ms."""
    _RATE_LIMITER.reset_for_tests()


def extract_pdf_url(payload: dict[str, Any]) -> str | None:
    """Return `best_oa_location.url_for_pdf` from an Unpaywall payload."""
    best = payload.get("best_oa_location") or {}
    return best.get("url_for_pdf") or None
