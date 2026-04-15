"""Unpaywall client — DOI-based open-access PDF lookup.

Free API, requires an email address as a query parameter (which is
also used as the identifier for reporting abuse). 100k requests /
day, 100 ms recommended between requests. Docs:
https://unpaywall.org/products/api/v2
"""

from __future__ import annotations

from typing import Any

import httpx

from quelle.repositories.errors import ConfigError
from quelle.repositories.http_client import get_json
from quelle.settings import Settings

API_URL = "https://api.unpaywall.org/v2/{doi}"


def lookup_by_doi(client: httpx.Client, settings: Settings, doi: str) -> dict[str, Any]:
    """Return the raw Unpaywall payload for a DOI.

    Raises `ConfigError` when no Unpaywall email is configured.
    Returns an empty dict when Unpaywall has no record (HTTP 404).
    """
    email = settings.unpaywall_email or settings.contact_email
    if not email:
        raise ConfigError(
            "Unpaywall requires an email — set UNPAYWALL_EMAIL or QUELLE_CONTACT_EMAIL"
        )
    url = API_URL.format(doi=doi)
    try:
        return get_json(client, url, params={"email": email})
    except Exception:  # noqa: BLE001
        return {}


def extract_pdf_url(payload: dict[str, Any]) -> str | None:
    """Return `best_oa_location.url_for_pdf` from an Unpaywall payload."""
    best = payload.get("best_oa_location") or {}
    return best.get("url_for_pdf") or None
