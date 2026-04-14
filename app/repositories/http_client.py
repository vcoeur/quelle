"""Shared httpx client + thin GET helper.

Every source module in `app/repositories/sources/` uses `build_client`
to construct a configured `httpx.Client` and `get_json` to make
requests with consistent error handling. Polite-pool participation
is baked into the User-Agent via `Settings.user_agent`.
"""

from __future__ import annotations

import httpx

from app.repositories.errors import NetworkError, RateLimitError
from app.settings import Settings


def build_client(settings: Settings) -> httpx.Client:
    """Return an httpx.Client configured with timeout and a polite User-Agent."""
    return httpx.Client(
        timeout=settings.http_timeout,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    )


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> dict:
    """GET a URL and return parsed JSON.

    Raises `RateLimitError` on 429, `NetworkError` on any other failure
    (request exception, non-2xx, or invalid JSON body).
    """
    try:
        response = client.get(url, params=params)
    except httpx.RequestError as exc:
        raise NetworkError(f"request failed: {exc}") from exc
    if response.status_code == 429:
        raise RateLimitError(f"rate limited by {url}: {response.text[:200]}")
    if response.status_code >= 400:
        raise NetworkError(f"{response.status_code} from {url}: {response.text[:200]}")
    try:
        return response.json()
    except ValueError as exc:
        raise NetworkError(f"invalid JSON from {url}: {exc}") from exc
