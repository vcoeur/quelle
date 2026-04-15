"""arXiv client — preprint metadata and direct PDF resolution.

Free API, no auth. Atom 1.0 responses parsed with stdlib
`xml.etree.ElementTree` so we don't take a new dependency.

**Rate limit:** arXiv asks clients to make no more than one metadata
request every 3 seconds per:
https://info.arxiv.org/help/api/user-manual.html . A single module
level `_LAST_CALL_AT` tracks the previous call and sleeps for the
remainder if the caller comes back too soon. Static PDF fetches
from `arxiv.org/pdf/...` are not subject to this limit and happen
through the generic PDF downloader.
"""

from __future__ import annotations

import threading
import time
from xml.etree import ElementTree as ET

import httpx

from quelle.models.publication import Author, Publication
from quelle.repositories.errors import NetworkError, NotFoundError
from quelle.settings import Settings

QUERY_URL = "https://export.arxiv.org/api/query"

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

_MIN_INTERVAL_SECONDS = 3.0
_LAST_CALL_AT = 0.0
_RATE_LOCK = threading.Lock()


def _rate_limit() -> None:
    """Sleep just long enough to respect arXiv's 3-second rule."""
    global _LAST_CALL_AT
    with _RATE_LOCK:
        now = time.monotonic()
        elapsed = now - _LAST_CALL_AT
        if elapsed < _MIN_INTERVAL_SECONDS:
            time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
        _LAST_CALL_AT = time.monotonic()


def fetch_by_arxiv_id(client: httpx.Client, settings: Settings, arxiv_id: str) -> Publication:
    """Return the arXiv record for a specific id.

    Strips any version suffix (`v3`) before querying — we always want
    the latest metadata for the given id.
    """
    del settings  # arXiv requires no auth / config
    bare_id = _strip_version(arxiv_id)
    _rate_limit()
    try:
        response = client.get(QUERY_URL, params={"id_list": bare_id})
    except httpx.RequestError as exc:
        raise NetworkError(f"arXiv request failed: {exc}") from exc
    if response.status_code >= 400:
        raise NetworkError(f"{response.status_code} from arXiv: {response.text[:200]}")
    return _parse_feed(response.text, expected_id=bare_id)


def search_by_title(client: httpx.Client, settings: Settings, title: str) -> Publication:
    """Return the first arXiv match for a title query."""
    del settings
    _rate_limit()
    try:
        response = client.get(
            QUERY_URL,
            params={"search_query": f"ti:{title}", "max_results": "1"},
        )
    except httpx.RequestError as exc:
        raise NetworkError(f"arXiv request failed: {exc}") from exc
    if response.status_code >= 400:
        raise NetworkError(f"{response.status_code} from arXiv: {response.text[:200]}")
    return _parse_feed(response.text)


def _strip_version(arxiv_id: str) -> str:
    """Drop the `vN` suffix if present. `1706.03762v5` -> `1706.03762`."""
    cleaned = arxiv_id.strip().lower()
    if "v" in cleaned:
        head, sep, tail = cleaned.rpartition("v")
        if sep and tail.isdigit():
            return head
    return cleaned


def _parse_feed(body: str, *, expected_id: str | None = None) -> Publication:
    """Parse an arXiv Atom feed and return the first entry as a Publication."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NetworkError(f"invalid arXiv Atom feed: {exc}") from exc

    entry = root.find("atom:entry", _ATOM_NS)
    if entry is None:
        raise NotFoundError(
            f"no arXiv entry for id: {expected_id}"
            if expected_id
            else "no arXiv entries in response"
        )
    return _entry_to_publication(entry)


def _entry_to_publication(entry: ET.Element) -> Publication:
    """Map an `<entry>` element into a `Publication`."""
    title = _text(entry.find("atom:title", _ATOM_NS)) or ""
    summary = _text(entry.find("atom:summary", _ATOM_NS))
    published = _text(entry.find("atom:published", _ATOM_NS))
    year = int(published.split("-", 1)[0]) if published else None

    authors: list[Author] = []
    for author in entry.findall("atom:author", _ATOM_NS):
        name = _text(author.find("atom:name", _ATOM_NS))
        if name:
            authors.append(Author(name=name))

    entry_id = _text(entry.find("atom:id", _ATOM_NS)) or ""
    arxiv_id = _arxiv_id_from_abs_url(entry_id)

    pdf_url: str | None = None
    for link in entry.findall("atom:link", _ATOM_NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href")
            break
    if pdf_url is None and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    return Publication(
        title=_normalise_whitespace(title),
        authors=authors,
        year=year,
        venue="arXiv preprint",
        publisher="arXiv",
        arxiv_id=arxiv_id,
        abstract=_normalise_whitespace(summary) if summary else None,
        is_open_access=True,
        pdf_url=pdf_url,
        source_url=entry_id or None,
        resolved_from_chain=["arxiv"],
    )


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text


def _normalise_whitespace(value: str) -> str:
    """Collapse runs of whitespace — arXiv wraps titles/abstracts at column 72."""
    return " ".join(value.split())


def _arxiv_id_from_abs_url(url: str) -> str | None:
    """Extract the bare id from an `http(s)://arxiv.org/abs/<id>[vN]` URL."""
    if not url:
        return None
    lowered = url.lower()
    marker = "arxiv.org/abs/"
    if marker not in lowered:
        return None
    tail = lowered.split(marker, 1)[1].split("?")[0].rstrip("/")
    return _strip_version(tail)


def _reset_rate_limit_for_tests() -> None:
    """Test hook — clears the last-call timestamp so tests don't pay 3s."""
    global _LAST_CALL_AT
    with _RATE_LOCK:
        _LAST_CALL_AT = 0.0


def _set_last_call_for_tests(value: float) -> None:
    """Test hook — pin the last-call timestamp."""
    global _LAST_CALL_AT
    with _RATE_LOCK:
        _LAST_CALL_AT = value
