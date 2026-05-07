"""Open Library client — primary source for book metadata.

Free, no key, broad ISBN coverage. The API is documented at
https://openlibrary.org/dev/docs/api/books and returns three loosely
related entity types:

- *Editions* (`/isbn/<isbn>.json`, `/books/OL...M.json`) — physical
  book records, ISBN-keyed, hold the publisher / page count / cover.
- *Works* (`/works/OL...W.json`) — abstract titles spanning multiple
  editions; hold subjects and the canonical description.
- *Authors* (`/authors/OL...A.json`) — author names, only referenced
  by key from editions and works.

`fetch_by_isbn` resolves the edition, then chases the linked work and
author records to fill author names and subjects in a single call
graph. Network failures on those secondary fetches degrade
gracefully — the edition record alone is still useful.
"""

from __future__ import annotations

from typing import Any

import httpx

from quelle.models.publication import Author, Publication
from quelle.models.search import SearchHit
from quelle.repositories.errors import NetworkError, NotFoundError, PublicationsError
from quelle.repositories.http_client import get_json
from quelle.settings import Settings

BASE_URL = "https://openlibrary.org"
SOURCE_NAME = "open_library"


def fetch_by_isbn(client: httpx.Client, settings: Settings, isbn: str) -> Publication:
    """Return the Open Library edition for a specific ISBN.

    Accepts a normalised digits-only ISBN-10 or ISBN-13 — the
    resolver strips hyphens before calling.
    """
    url = f"{BASE_URL}/isbn/{isbn}.json"
    try:
        edition = get_json(client, url)
    except NetworkError as exc:
        if "404" in str(exc):
            raise NotFoundError(f"no Open Library edition for ISBN: {isbn}") from exc
        raise
    return _build_publication(client, settings, edition)


def search_by_title(client: httpx.Client, settings: Settings, title: str) -> Publication:
    """Return the top Open Library match for a free-text title."""
    url = f"{BASE_URL}/search.json"
    payload = get_json(client, url, params={"title": title, "limit": "1"})
    docs = payload.get("docs") or []
    if not docs:
        raise NotFoundError(f"no Open Library match for title: {title!r}")
    return _doc_to_publication(docs[0])


def search(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    author: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Return up to `limit` candidate book hits for a free-text title query.

    `kind` is accepted for signature uniformity but ignored — Open
    Library only indexes books.
    """
    del settings
    del kind
    params: dict[str, str] = {"title": query, "limit": str(limit)}
    if author:
        params["author"] = author
    payload = get_json(client, f"{BASE_URL}/search.json", params=params)
    docs = payload.get("docs") or []
    return [_to_search_hit(doc, rank) for rank, doc in enumerate(docs)]


def _to_search_hit(doc: dict[str, Any], rank: int) -> SearchHit:
    """Map a `search.json` doc into a SearchHit."""
    isbn_list = doc.get("isbn") or []
    isbn_10 = next((value for value in isbn_list if len(value) == 10), None)
    isbn_13 = next((value for value in isbn_list if len(value) == 13), None)

    authors = [Author(name=name) for name in doc.get("author_name") or [] if name]

    return SearchHit(
        title=doc.get("title") or "",
        authors=authors,
        year=doc.get("first_publish_year"),
        type="book",
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        source=SOURCE_NAME,
        source_id=doc.get("key") or "",
        raw_rank=rank,
    )


def _build_publication(
    client: httpx.Client,
    settings: Settings,
    edition: dict[str, Any],
) -> Publication:
    """Compose a Publication from an edition + chased work + authors."""
    authors = _resolve_authors(client, edition)
    work = _resolve_first_work(client, edition)
    return _to_publication(edition, work=work, authors=authors)


def _resolve_authors(client: httpx.Client, edition: dict[str, Any]) -> list[Author]:
    """Fetch each author record by key; skip any that fail individually."""
    authors: list[Author] = []
    for entry in edition.get("authors") or []:
        key = entry.get("key") if isinstance(entry, dict) else None
        if not key:
            continue
        try:
            record = get_json(client, f"{BASE_URL}{key}.json")
        except PublicationsError:
            continue
        name = record.get("name") or record.get("personal_name")
        if name:
            authors.append(Author(name=name))
    return authors


def _resolve_first_work(client: httpx.Client, edition: dict[str, Any]) -> dict[str, Any]:
    """Fetch the first linked work record, or {} on miss."""
    works = edition.get("works") or []
    if not works:
        return {}
    key = works[0].get("key") if isinstance(works[0], dict) else None
    if not key:
        return {}
    try:
        return get_json(client, f"{BASE_URL}{key}.json")
    except PublicationsError:
        return {}


def _to_publication(
    edition: dict[str, Any],
    *,
    work: dict[str, Any] | None = None,
    authors: list[Author] | None = None,
) -> Publication:
    """Map an Open Library edition (+ optional work + authors) into a Publication."""
    work = work or {}
    isbn_10 = _first(edition.get("isbn_10"))
    isbn_13 = _first(edition.get("isbn_13"))

    publishers = edition.get("publishers") or []
    publisher = publishers[0] if publishers else None

    subjects = list(edition.get("subjects") or work.get("subjects") or [])

    abstract = _description_text(work.get("description")) or _description_text(
        edition.get("description")
    )

    return Publication(
        title=edition.get("title") or work.get("title") or "",
        authors=list(authors or []),
        year=_publish_year(edition.get("publish_date")),
        publisher=publisher,
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        edition=edition.get("edition_name"),
        page_count=edition.get("number_of_pages"),
        kind="book",
        subjects=subjects,
        abstract=abstract,
        source_url=f"{BASE_URL}{edition['key']}" if edition.get("key") else None,
        resolved_from_chain=["open_library"],
    )


def _doc_to_publication(doc: dict[str, Any]) -> Publication:
    """Map a `search.json` doc (search-index shape, lighter than an edition)."""
    isbn_list = doc.get("isbn") or []
    isbn_10 = next((value for value in isbn_list if len(value) == 10), None)
    isbn_13 = next((value for value in isbn_list if len(value) == 13), None)

    publishers = doc.get("publisher") or []
    publisher = publishers[0] if publishers else None

    authors = [Author(name=name) for name in doc.get("author_name") or [] if name]
    subjects = list(doc.get("subject") or [])

    return Publication(
        title=doc.get("title") or "",
        authors=authors,
        year=doc.get("first_publish_year"),
        publisher=publisher,
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        page_count=doc.get("number_of_pages_median"),
        kind="book",
        subjects=subjects,
        source_url=f"{BASE_URL}{doc['key']}" if doc.get("key") else None,
        resolved_from_chain=["open_library"],
    )


def _first(value: list | None) -> str | None:
    return value[0] if value else None


def _publish_year(publish_date: str | None) -> int | None:
    """Pull the trailing 4-digit year out of free-form publish_date strings."""
    if not publish_date:
        return None
    for token in reversed(publish_date.replace(",", " ").split()):
        if len(token) == 4 and token.isdigit():
            return int(token)
    return None


def _description_text(value: object) -> str | None:
    """Open Library returns description as either a string or `{type, value}`."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        text = value.get("value")
        return text or None
    return None
