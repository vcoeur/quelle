"""Google Books client — book metadata fallback when Open Library is sparse.

Public Volumes API (`https://www.googleapis.com/books/v1/volumes`).
No key required for low-volume reads (1k requests/day per IP), but
an optional `GOOGLE_BOOKS_API_KEY` is honoured for higher quotas.
Schema: https://developers.google.com/books/docs/v1/reference/volumes
"""

from __future__ import annotations

from typing import Any

import httpx

from quelle.models.publication import Author, Publication
from quelle.models.search import SearchHit
from quelle.repositories.errors import NotFoundError
from quelle.repositories.http_client import get_json
from quelle.settings import Settings

VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"
SOURCE_NAME = "google_books"


def _auth_params(settings: Settings) -> dict[str, str]:
    if settings.google_books_api_key:
        return {"key": settings.google_books_api_key}
    return {}


def fetch_by_isbn(client: httpx.Client, settings: Settings, isbn: str) -> Publication:
    """Return the top Google Books volume for a specific ISBN."""
    params = {"q": f"isbn:{isbn}", "maxResults": "1", **_auth_params(settings)}
    payload = get_json(client, VOLUMES_URL, params=params)
    items = payload.get("items") or []
    if not items:
        raise NotFoundError(f"no Google Books volume for ISBN: {isbn}")
    return _to_publication(items[0])


def search_by_title(client: httpx.Client, settings: Settings, title: str) -> Publication:
    """Return the top Google Books volume for a free-text title query."""
    params = {"q": f"intitle:{title}", "maxResults": "1", **_auth_params(settings)}
    payload = get_json(client, VOLUMES_URL, params=params)
    items = payload.get("items") or []
    if not items:
        raise NotFoundError(f"no Google Books match for title: {title!r}")
    return _to_publication(items[0])


def search(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    author: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Return up to `limit` candidate hits for a free-text query.

    Uses Google Books' field qualifiers (`intitle:` for the query and
    `inauthor:` when an author hint is provided) so the underlying
    relevance ranker biases on each field rather than mashing them
    into a single bag-of-words.
    """
    parts = [f"intitle:{query}"]
    if author:
        parts.append(f"inauthor:{author}")
    params = {"q": "+".join(parts), "maxResults": str(limit), **_auth_params(settings)}
    payload = get_json(client, VOLUMES_URL, params=params)
    items = payload.get("items") or []
    return [_to_search_hit(item, rank) for rank, item in enumerate(items)]


def _to_search_hit(item: dict[str, Any], rank: int) -> SearchHit:
    """Map a Google Books `volumes` item into a SearchHit."""
    volume = item.get("volumeInfo") or {}
    isbn_10, isbn_13 = _extract_isbns(volume.get("industryIdentifiers") or [])
    authors = [Author(name=name) for name in volume.get("authors") or [] if name]

    return SearchHit(
        title=volume.get("title") or "",
        authors=authors,
        year=_publish_year(volume.get("publishedDate")),
        type="book",
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        source=SOURCE_NAME,
        source_id=item.get("id") or "",
        raw_rank=rank,
    )


def _to_publication(item: dict[str, Any]) -> Publication:
    """Map a Google Books `volumes` item into a Publication."""
    volume = item.get("volumeInfo") or {}
    access = item.get("accessInfo") or {}

    isbn_10, isbn_13 = _extract_isbns(volume.get("industryIdentifiers") or [])

    authors = [Author(name=name) for name in volume.get("authors") or [] if name]
    subjects = list(volume.get("categories") or [])

    pdf_block = access.get("pdf") or {}
    is_public_domain = access.get("accessViewStatus") == "FULL_PUBLIC_DOMAIN"
    is_oa = bool(pdf_block.get("isAvailable")) and is_public_domain
    pdf_url = pdf_block.get("downloadLink") if is_oa else None

    return Publication(
        title=volume.get("title") or "",
        authors=authors,
        year=_publish_year(volume.get("publishedDate")),
        publisher=volume.get("publisher"),
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        page_count=volume.get("pageCount"),
        kind="book",
        subjects=subjects,
        abstract=volume.get("description"),
        is_open_access=is_oa or None,
        pdf_url=pdf_url,
        source_url=volume.get("infoLink") or volume.get("canonicalVolumeLink"),
        resolved_from_chain=["google_books"],
    )


def _extract_isbns(identifiers: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Pull ISBN-10 and ISBN-13 out of the `industryIdentifiers` list."""
    isbn_10: str | None = None
    isbn_13: str | None = None
    for entry in identifiers:
        kind = entry.get("type")
        value = entry.get("identifier")
        if not value:
            continue
        if kind == "ISBN_10":
            isbn_10 = value
        elif kind == "ISBN_13":
            isbn_13 = value
    return isbn_10, isbn_13


def _publish_year(value: str | None) -> int | None:
    """Google Books returns either `YYYY`, `YYYY-MM`, or `YYYY-MM-DD`."""
    if not value:
        return None
    head = value.split("-", 1)[0]
    return int(head) if head.isdigit() and len(head) == 4 else None
