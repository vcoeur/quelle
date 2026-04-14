"""Resolution chain — turn user input into a normalised `Publication`.

Two public entry points:

- `resolve(client, settings, query)` — single-source resolution.
  Picks the right source based on the shape of the query and returns
  whatever it finds.
- `resolve_with_enrichment(client, settings, query)` — runs
  `resolve` and then fills missing fields from secondary sources
  (Crossref, Semantic Scholar) when possible. This is what the CLI
  `fetch` command uses.

The resolver never touches the local cache; caching is bolted on
inside `app.services.resolver` once Phase 2 lands (not here — keep
this module focused on source orchestration).
"""

from __future__ import annotations

import re

import httpx

from app.models.publication import Publication
from app.repositories.cache import Cache
from app.repositories.errors import NetworkError, NotFoundError, PublicationsError
from app.repositories.sources import arxiv, crossref, openalex, semantic_scholar
from app.settings import Settings

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5}(v\d+)?|[a-z\-]+/\d{7}(v\d+)?)$", re.IGNORECASE)
_SCHOLAR_HOST_RE = re.compile(r"scholar\.google\.\w+", re.IGNORECASE)


def resolve(client: httpx.Client, settings: Settings, query: str) -> Publication:
    """Route a query to a single source and return its Publication."""
    stripped = query.strip()

    if _SCHOLAR_HOST_RE.search(stripped):
        from app.repositories.sources import scholar_fallback

        title = scholar_fallback.extract_title_from_scholar_url(stripped)
        return openalex.search_by_title(client, settings, title)

    doi_candidate = _extract_doi(stripped)
    if doi_candidate:
        return openalex.fetch_by_doi(client, settings, doi_candidate)

    if _ARXIV_RE.match(stripped):
        return arxiv.fetch_by_arxiv_id(client, settings, stripped)

    return openalex.search_by_title(client, settings, stripped)


def resolve_with_enrichment(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    cache: Cache | None = None,
) -> Publication:
    """Resolve then enrich.

    If `cache` is provided, check it by DOI / OpenAlex id / arXiv id
    / exact title before calling any upstream source. On a miss, run
    the full chain and upsert the result.

    1. Run the primary resolver.
    2. If we started on arXiv and have no DOI, try OpenAlex title
       search to find a published version.
    3. If we have a DOI and an abstract or venue is missing, try
       Crossref.
    4. If the abstract is still missing and we have a DOI, try
       Semantic Scholar.
    """
    if cache is not None:
        hit = _lookup_in_cache(cache, query)
        if hit is not None:
            return hit

    primary = resolve(client, settings, query)
    current = primary

    chain = primary.resolved_from_chain
    started_on_arxiv = bool(chain) and chain[0] == "arxiv"
    if started_on_arxiv and not current.doi:
        current = _try_enrich(
            current,
            lambda: openalex.search_by_title(client, settings, current.title),
        )

    if current.doi and (current.abstract is None or not current.venue):
        current = _try_enrich(
            current,
            lambda: crossref.fetch_by_doi(client, settings, current.doi),
        )

    if current.doi and current.abstract is None:
        current = _try_enrich(
            current,
            lambda: semantic_scholar.fetch_by_doi(client, settings, current.doi),
        )

    if cache is not None:
        cache.upsert(current)
    return current


def _lookup_in_cache(cache: Cache, query: str) -> Publication | None:
    """Try every cache lookup route for the given query string."""
    stripped = query.strip()
    doi = _extract_doi(stripped)
    if doi:
        hit = cache.get_by_doi(doi)
        if hit is not None:
            return hit
    if _ARXIV_RE.match(stripped):
        hit = cache.get_by_arxiv_id(arxiv._strip_version(stripped))
        if hit is not None:
            return hit
    if stripped.startswith("https://openalex.org/") or stripped.startswith("openalex:"):
        hit = cache.get_by_openalex_id(
            stripped.removeprefix("openalex:") if stripped.startswith("openalex:") else stripped
        )
        if hit is not None:
            return hit
    return cache.get_by_title_exact(stripped)


def _try_enrich(current: Publication, fetch):
    """Run `fetch`, merge its result into `current`, swallow failures."""
    try:
        other = fetch()
    except (NotFoundError, NetworkError, PublicationsError):
        return current
    return current.merged_with(other)


def _extract_doi(query: str) -> str | None:
    """Pull a bare DOI out of a DOI URL or raw query if one is present."""
    lowered = query.lower()
    lowered = lowered.removeprefix("https://doi.org/")
    lowered = lowered.removeprefix("http://doi.org/")
    lowered = lowered.removeprefix("doi:")
    if _DOI_RE.match(lowered):
        return lowered
    return None
