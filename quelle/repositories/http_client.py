"""Shared httpx client + thin GET helpers.

Every source module in `app/repositories/sources/` uses `build_client`
to construct a configured `httpx.Client` and `get_json` / `get_text` /
`get_bytes` to make requests with consistent error handling. Polite-pool
participation is baked into the User-Agent via `Settings.user_agent`.
"""

from __future__ import annotations

import sys

import httpx

from quelle.repositories.errors import NetworkError, RateLimitError
from quelle.settings import Settings


def build_client(settings: Settings) -> httpx.Client:
    """Return an httpx.Client configured with timeout and a polite User-Agent.

    With no contact email configured, a one-line warning goes to stderr
    (once per client, never stdout — `--json` output stays clean): the
    Crossref / OpenAlex polite pools identify callers by the mailto in
    the User-Agent, and anonymous traffic gets the slow lane.
    """
    if not settings.contact_email:
        print(
            "quelle: warning: QUELLE_CONTACT_EMAIL is not set — Crossref/OpenAlex "
            "polite-pool identification is off; set it via `quelle config edit`.",
            file=sys.stderr,
        )
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
    headers: dict[str, str] | None = None,
) -> dict:
    """GET a URL and return parsed JSON.

    Raises `RateLimitError` on 429, `NetworkError` on any other failure
    (request exception, non-2xx, or invalid JSON body). Non-2xx errors
    carry the upstream status in `NetworkError.status_code`.
    """
    response = _get(client, url, params=params, headers=headers)
    try:
        return response.json()
    except ValueError as exc:
        raise NetworkError(f"invalid JSON from {url}: {exc}") from exc


def get_text(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """GET a URL and return the decoded response body.

    Used for HTML scraping (the Open-Graph URL resolver). The same
    error-mapping rules as `get_json` apply.
    """
    return _get(client, url, params=params, headers=headers).text


def get_bytes(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET a URL and return the raw response body.

    Used for sources that return XML (arXiv Atom, BnF SRU) — handing
    bytes to ElementTree lets the parser honour the XML declaration's
    charset instead of httpx's guess. The same error-mapping rules as
    `get_json` apply.
    """
    return _get(client, url, params=params, headers=headers).content


def _get(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Issue the GET and convert HTTP-level failures into our error types."""
    try:
        response = client.get(url, params=params, headers=headers)
    except httpx.RequestError as exc:
        raise NetworkError(f"request failed: {exc}") from exc
    if response.status_code == 429:
        raise RateLimitError(
            f"rate limited by {url}: {response.text[:200]}",
            status_code=429,
        )
    if response.status_code >= 400:
        raise NetworkError(
            f"{response.status_code} from {url}: {response.text[:200]}",
            status_code=response.status_code,
        )
    return response
