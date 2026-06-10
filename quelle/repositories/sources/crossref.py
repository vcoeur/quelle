"""Crossref client — DOI-authoritative metadata corroboration.

Free REST API, no signup. Every request joins the polite pool via
`mailto=<contact_email>` (either as a query param, as done here, or
as a suffix on the User-Agent). Crossref's response shape is
documented at https://api.crossref.org/swagger-ui/index.html — the
interesting payload lives under `message`.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from quelle.models.publication import Author, Publication
from quelle.repositories.errors import NetworkError, NotFoundError
from quelle.repositories.http_client import get_json
from quelle.settings import Settings

WORKS_URL = "https://api.crossref.org/works"

_JATS_TAG_RE = re.compile(r"<[^>]+>")


def _polite_params(settings: Settings) -> dict[str, str]:
    params: dict[str, str] = {}
    if settings.contact_email:
        params["mailto"] = settings.contact_email
    return params


def fetch_by_doi(client: httpx.Client, settings: Settings, doi: str) -> Publication:
    """Return the Crossref record for a specific DOI.

    Accepts a bare DOI (`10.xxxx/yyyy`). The caller should strip any
    `https://doi.org/` prefix first.
    """
    url = f"{WORKS_URL}/{quote(doi, safe='/')}"
    try:
        payload = get_json(client, url, params=_polite_params(settings))
    except NetworkError as exc:
        if exc.status_code == 404:
            raise NotFoundError(f"no Crossref record for DOI: {doi}") from exc
        raise
    message = payload.get("message")
    if not message:
        raise NotFoundError(f"no Crossref record for DOI: {doi}")
    return _to_publication(message)


def _to_publication(message: dict[str, Any]) -> Publication:
    """Map a Crossref `message` block into a `Publication`."""
    titles = message.get("title") or []
    title = titles[0] if titles else ""

    authors: list[Author] = []
    for entry in message.get("author") or []:
        given = entry.get("given") or ""
        family = entry.get("family") or ""
        name = f"{given} {family}".strip() or entry.get("name") or ""
        if not name:
            continue
        affiliations = entry.get("affiliation") or []
        affiliation = None
        if affiliations:
            affiliation = affiliations[0].get("name") if isinstance(affiliations[0], dict) else None
        authors.append(Author(name=name, orcid=entry.get("ORCID"), affiliation=affiliation))

    year = _extract_year(message)

    containers = message.get("container-title") or []
    venue = containers[0] if containers else None

    return Publication(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        publisher=message.get("publisher"),
        doi=(message.get("DOI") or "").lower() or None,
        abstract=_strip_jats(message.get("abstract")),
        citation_count=message.get("is-referenced-by-count"),
        is_open_access=None,  # Crossref has no direct OA flag; Unpaywall owns that
        pdf_url=_extract_pdf_link(message),
        source_url=_source_url(message),
        topics=message.get("subject") or [],
        journal_volume=message.get("volume"),
        journal_issue=message.get("issue"),
        page_range=message.get("page"),
        resolved_from_chain=["crossref"],
    )


def _extract_year(message: dict[str, Any]) -> int | None:
    """Pull the year from Crossref's multi-layer date fields, taking the
    first populated one in fixed priority order: published-print, then
    published-online, issued, created.
    """
    for key in ("published-print", "published-online", "issued", "created"):
        block = message.get(key)
        if not block:
            continue
        date_parts = block.get("date-parts")
        if date_parts and date_parts[0]:
            first = date_parts[0][0]
            if isinstance(first, int):
                return first
    return None


def _strip_jats(abstract: str | None) -> str | None:
    """Remove JATS XML tags that Crossref wraps abstracts in."""
    if not abstract:
        return None
    cleaned = _JATS_TAG_RE.sub("", abstract).strip()
    return cleaned or None


def _extract_pdf_link(message: dict[str, Any]) -> str | None:
    """Return the first `link` entry whose content-type is PDF, if any."""
    for link in message.get("link") or []:
        if (link.get("content-type") or "").lower() == "application/pdf":
            return link.get("URL")
    return None


def _source_url(message: dict[str, Any]) -> str | None:
    """Prefer the Crossref `URL` field; fall back to a doi.org URL."""
    if message.get("URL"):
        return message["URL"]
    doi = message.get("DOI")
    return f"https://doi.org/{doi}" if doi else None
