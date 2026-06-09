"""OpenAlex client — primary source for publication metadata.

Free API with a $1/day quota on an optional API key. The work object
shape is documented at:
    https://docs.openalex.org/api-entities/works/work-object
"""

from __future__ import annotations

from typing import Any

import httpx

from quelle.models.publication import Author, Publication
from quelle.models.search import HitType, SearchHit
from quelle.repositories.errors import NotFoundError
from quelle.repositories.http_client import get_json
from quelle.settings import Settings

WORKS_URL = "https://api.openalex.org/works"
SOURCE_NAME = "openalex"


def _auth_params(settings: Settings) -> dict[str, str]:
    """Query params that apply to every OpenAlex request."""
    params: dict[str, str] = {}
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    if settings.contact_email:
        params["mailto"] = settings.contact_email
    return params


def search_by_title(client: httpx.Client, settings: Settings, title: str) -> Publication:
    """Return the top OpenAlex match for a free-text title query."""
    params = {"search": title, "per-page": "1", **_auth_params(settings)}
    payload = get_json(client, WORKS_URL, params=params)
    results = payload.get("results") or []
    if not results:
        raise NotFoundError(f"no OpenAlex match for title: {title!r}")
    return _to_publication(results[0])


def fetch_by_doi(client: httpx.Client, settings: Settings, doi: str) -> Publication:
    """Return the OpenAlex work for a specific DOI.

    Accepts a bare DOI (`10.xxxx/yyyy`) — callers should normalise
    first. OpenAlex also accepts full `https://doi.org/...` URLs on
    this endpoint, but we keep the call site simple.
    """
    url = f"{WORKS_URL}/doi:{doi}"
    payload = get_json(client, url, params=_auth_params(settings))
    return _to_publication(payload)


def fetch_by_isbn(client: httpx.Client, settings: Settings, isbn: str) -> Publication:
    """Best-effort OpenAlex lookup for a book ISBN.

    OpenAlex does not carry ISBN as a first-class identifier on Work
    records — the lookup degrades to a free-text `search=<isbn>` over
    the work index, restricted to book / book-chapter types. That
    matches when the ISBN appears in title / abstract / source name,
    but it is **prone to false positives**: a book chapter that
    references another book by ISBN can rank first. Callers should
    treat results with skepticism — the resolver places this source
    last in the book fallback chain for that reason.
    """
    params = {
        "search": isbn,
        "filter": "type:book|book-chapter",
        "per-page": "1",
        **_auth_params(settings),
    }
    payload = get_json(client, WORKS_URL, params=params)
    results = payload.get("results") or []
    if not results:
        raise NotFoundError(f"no OpenAlex book match for ISBN: {isbn}")
    return _to_publication(results[0])


def search(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    author: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Return up to `limit` candidate hits for a free-text query.

    `author`, when provided, narrows results via OpenAlex's
    `author.display_name.search` filter. `kind` (one of `book` /
    `article`) adds a `type:` filter so OpenAlex's relevance ranker
    doesn't surface articles when the user explicitly asked for books
    or vice versa.
    """
    filters: list[str] = []
    if author:
        filters.append(f"author.display_name.search:{author}")
    if kind == "book":
        filters.append("type:book|book-chapter")
    elif kind == "article":
        filters.append("type:article|preprint")

    params: dict[str, str] = {
        "search": query,
        "per-page": str(limit),
        **_auth_params(settings),
    }
    if filters:
        params["filter"] = ",".join(filters)
    payload = get_json(client, WORKS_URL, params=params)
    results = payload.get("results") or []
    return [_to_search_hit(work, rank) for rank, work in enumerate(results)]


def _to_search_hit(work: dict[str, Any], rank: int) -> SearchHit:
    """Map an OpenAlex work JSON into a `SearchHit`."""
    authorships = work.get("authorships") or []
    authors: list[Author] = []
    for entry in authorships:
        author = entry.get("author") or {}
        name = author.get("display_name") or ""
        if name:
            authors.append(Author(name=name, orcid=author.get("orcid")))

    work_type = _normalise_kind(work.get("type"))
    hit_type: HitType
    if work_type in {"book", "book-chapter"}:
        hit_type = "book"
    elif work_type in {"article", "preprint"}:
        hit_type = "article"
    else:
        hit_type = "unknown"

    return SearchHit(
        title=work.get("title") or work.get("display_name") or "",
        authors=authors,
        year=work.get("publication_year"),
        type=hit_type,
        doi=_strip_doi(work.get("doi")),
        arxiv_id=_extract_arxiv_id(work),
        source=SOURCE_NAME,
        source_id=work.get("id") or "",
        raw_rank=rank,
    )


def _to_publication(work: dict[str, Any]) -> Publication:
    """Map an OpenAlex work JSON into a `Publication`."""
    authorships = work.get("authorships") or []
    authors: list[Author] = []
    for entry in authorships:
        author = entry.get("author") or {}
        # Whitespace-only display names are as unusable as missing ones —
        # strip so they are rejected the same way.
        name = (author.get("display_name") or "").strip()
        if not name:
            continue
        institutions = entry.get("institutions") or []
        affiliation = institutions[0].get("display_name") if institutions else None
        authors.append(
            Author(
                name=name,
                orcid=author.get("orcid"),
                affiliation=affiliation,
            )
        )

    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    venue = source.get("display_name")
    publisher = source.get("host_organization_name")

    open_access = work.get("open_access") or {}
    best_oa = work.get("best_oa_location") or {}
    pdf_url = best_oa.get("pdf_url") or open_access.get("oa_url")

    topics = [
        topic.get("display_name")
        for topic in (work.get("topics") or [])
        if topic.get("display_name")
    ]

    biblio = work.get("biblio") or {}

    return Publication(
        title=work.get("title") or work.get("display_name") or "",
        authors=authors,
        year=work.get("publication_year"),
        venue=venue,
        publisher=publisher,
        doi=_strip_doi(work.get("doi")),
        arxiv_id=_extract_arxiv_id(work),
        openalex_id=work.get("id"),
        kind=_normalise_kind(work.get("type")),
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        citation_count=work.get("cited_by_count"),
        is_open_access=open_access.get("is_oa"),
        pdf_url=pdf_url,
        source_url=primary_location.get("landing_page_url") or work.get("id"),
        topics=topics,
        journal_volume=biblio.get("volume") or None,
        journal_issue=biblio.get("issue") or None,
        page_range=_format_pages(biblio),
        resolved_from_chain=["openalex"],
    )


def _normalise_kind(work_type: str | None) -> str | None:
    """Map OpenAlex `type` strings onto our `kind` enum.

    OpenAlex publishes a long type vocabulary; we collapse anything
    that isn't an article/preprint/book/book-chapter to None and let
    downstream code render it as untagged.
    """
    if work_type in {"article", "preprint", "book", "book-chapter"}:
        return work_type
    return None


def _format_pages(biblio: dict[str, Any]) -> str | None:
    """Compose a `first-last` page string from OpenAlex's biblio block."""
    first = biblio.get("first_page")
    last = biblio.get("last_page")
    if first and last:
        return f"{first}-{last}"
    return first or last or None


def _strip_doi(doi: str | None) -> str | None:
    """Normalise a DOI to the bare `10.xxxx/yyyy` form, lowercased."""
    if not doi:
        return None
    return doi.lower().removeprefix("https://doi.org/")


def _extract_arxiv_id(work: dict[str, Any]) -> str | None:
    """Best-effort arXiv id extraction from a work's locations."""
    for location in work.get("locations") or []:
        landing = (location.get("landing_page_url") or "").lower()
        if "arxiv.org/abs/" in landing:
            return landing.split("arxiv.org/abs/", 1)[1].split("?")[0].rstrip("/")
        pdf = (location.get("pdf_url") or "").lower()
        if "arxiv.org/pdf/" in pdf:
            tail = pdf.split("arxiv.org/pdf/", 1)[1]
            return tail.split("?")[0].rstrip("/").removesuffix(".pdf")
    return None


def _reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """Rebuild a readable abstract from OpenAlex's inverted-index representation.

    OpenAlex stores abstracts as `{word: [positions]}` to dodge scraper
    licences. Reconstruct by sorting positions and joining words back
    in order.
    """
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, indices in inverted.items():
        for idx in indices:
            positions.append((idx, word))
    positions.sort()
    return " ".join(word for _, word in positions)
