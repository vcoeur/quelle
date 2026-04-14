"""Semantic Scholar client — fallback metadata + citation graph.

Free API. Unauthenticated is fine for single-item usage; an optional
`SEMANTIC_SCHOLAR_API_KEY` unlocks higher rate limits. Docs:
https://api.semanticscholar.org/api-docs/graph
"""

from __future__ import annotations

from typing import Any

import httpx

from app.models.publication import Author, Publication
from app.repositories.errors import NotFoundError
from app.repositories.http_client import get_json
from app.settings import Settings

API_BASE = "https://api.semanticscholar.org/graph/v1"

_FIELDS = ",".join(
    [
        "paperId",
        "externalIds",
        "title",
        "abstract",
        "year",
        "authors",
        "authors.name",
        "authors.affiliations",
        "venue",
        "publicationVenue",
        "citationCount",
        "openAccessPdf",
        "url",
        "fieldsOfStudy",
    ]
)


def _auth_headers(settings: Settings) -> dict[str, str]:
    headers: dict[str, str] = {}
    key = getattr(settings, "semantic_scholar_api_key", "")
    if key:
        headers["x-api-key"] = key
    return headers


def search_by_title(client: httpx.Client, settings: Settings, title: str) -> Publication:
    """Return the single best Semantic Scholar match for a title."""
    url = f"{API_BASE}/paper/search/match"
    payload = get_json(
        client,
        url,
        params={"query": title, "fields": _FIELDS},
    )
    data = payload.get("data") or []
    if not data:
        raise NotFoundError(f"no Semantic Scholar match for title: {title!r}")
    return _to_publication(data[0])


def fetch_by_doi(client: httpx.Client, settings: Settings, doi: str) -> Publication:
    """Return the Semantic Scholar record for a specific DOI."""
    url = f"{API_BASE}/paper/DOI:{doi}"
    payload = get_json(client, url, params={"fields": _FIELDS})
    return _to_publication(payload)


def _to_publication(paper: dict[str, Any]) -> Publication:
    """Map a Semantic Scholar paper JSON into a `Publication`."""
    if not paper or paper.get("error"):
        raise NotFoundError(f"Semantic Scholar error: {paper.get('error') if paper else 'empty'}")

    authors: list[Author] = []
    for entry in paper.get("authors") or []:
        name = entry.get("name") or ""
        if not name:
            continue
        affiliations = entry.get("affiliations") or []
        affiliation = affiliations[0] if affiliations else None
        authors.append(Author(name=name, affiliation=affiliation))

    external = paper.get("externalIds") or {}
    doi = external.get("DOI")
    arxiv_id = external.get("ArXiv")

    venue_block = paper.get("publicationVenue") or {}
    venue = venue_block.get("name") or paper.get("venue") or None
    open_access_pdf = paper.get("openAccessPdf") or {}
    pdf_url = open_access_pdf.get("url")

    return Publication(
        title=paper.get("title") or "",
        authors=authors,
        year=paper.get("year"),
        venue=venue,
        publisher=venue_block.get("publisher"),
        doi=(doi or "").lower() or None,
        arxiv_id=arxiv_id,
        semantic_scholar_id=paper.get("paperId"),
        abstract=paper.get("abstract"),
        citation_count=paper.get("citationCount"),
        is_open_access=bool(pdf_url),
        pdf_url=pdf_url,
        source_url=paper.get("url"),
        topics=paper.get("fieldsOfStudy") or [],
        resolved_from_chain=["semantic_scholar"],
    )
