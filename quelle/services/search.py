"""Search orchestrator.

Fans out a single query to every wired source's `search()` adapter in
parallel, then dedups and ranks the combined results with Reciprocal
Rank Fusion (RRF). Failures from individual sources are caught and
logged — the search degrades to whatever sources did respond rather
than failing the whole call.

This module is the single place that knows the source set and the
fusion logic. Adapters stay ignorant of one another; the resolver
(used by `quelle fetch`) is independent and unaffected.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Literal

import httpx

from quelle.models.publication import Author
from quelle.models.search import HitType, MergedHit, SearchHit
from quelle.repositories.errors import PublicationsError, UserError
from quelle.repositories.sources import (
    arxiv,
    bnf,
    google_books,
    open_library,
    openalex,
    semantic_scholar,
)
from quelle.settings import Settings

logger = logging.getLogger(__name__)

SearchType = Literal["book", "article", "all"]

# Maps adapter slug -> (search callable, set of types the source covers).
SOURCES: dict[str, tuple[Callable[..., list[SearchHit]], set[HitType]]] = {
    openalex.SOURCE_NAME: (openalex.search, {"article", "book"}),
    semantic_scholar.SOURCE_NAME: (semantic_scholar.search, {"article"}),
    arxiv.SOURCE_NAME: (arxiv.search, {"article"}),
    open_library.SOURCE_NAME: (open_library.search, {"book"}),
    google_books.SOURCE_NAME: (google_books.search, {"book"}),
    bnf.SOURCE_NAME: (bnf.search, {"book"}),
}

_RRF_K = 60.0


def search(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    author: str | None = None,
    type: SearchType = "all",
    sources: list[str] | None = None,
    no_sources: list[str] | None = None,
    limit: int = 10,
) -> list[MergedHit]:
    """Run a multi-source search and return up to `limit` merged hits.

    Source set is selected from `SOURCES` by `type`, then narrowed by
    `sources` (allowlist) and `no_sources` (denylist) if provided.
    Per-source pull size is `max(limit * 2, 20)` so RRF has material
    to merge after dedup.
    """
    if limit < 1:
        raise UserError("--limit must be at least 1")

    selected = _select_sources(type, sources, no_sources)
    per_source_limit = max(limit * 2, 20)

    raw: list[list[SearchHit]] = []
    with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as pool:
        futures = {
            pool.submit(_safe_call, fn, client, settings, query, author, per_source_limit): name
            for name, fn in selected.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                hits = future.result()
            except Exception as exc:  # last-resort catch — _safe_call should swallow first
                logger.warning("source %s raised unexpectedly: %s", name, exc)
                continue
            raw.append(hits)

    merged = _merge(raw)
    merged.sort(key=_sort_key)
    return merged[:limit]


def _select_sources(
    type: SearchType,
    sources: list[str] | None,
    no_sources: list[str] | None,
) -> dict[str, Callable[..., list[SearchHit]]]:
    """Pick the source set for this run, validating any user filters."""
    allow = {name.strip() for name in sources or [] if name.strip()}
    deny = {name.strip() for name in no_sources or [] if name.strip()}
    unknown = (allow | deny) - SOURCES.keys()
    if unknown:
        raise UserError(f"unknown source(s): {', '.join(sorted(unknown))}")

    selected: dict[str, Callable[..., list[SearchHit]]] = {}
    for name, (fn, covers) in SOURCES.items():
        if type != "all" and type not in covers:
            continue
        if allow and name not in allow:
            continue
        if name in deny:
            continue
        selected[name] = fn

    if not selected:
        raise UserError("no sources selected after applying filters")
    return selected


def _safe_call(
    fn: Callable[..., list[SearchHit]],
    client: httpx.Client,
    settings: Settings,
    query: str,
    author: str | None,
    limit: int,
) -> list[SearchHit]:
    """Invoke a source's `search()` and swallow expected errors."""
    try:
        return fn(client, settings, query, author=author, limit=limit)
    except PublicationsError as exc:
        logger.info("source failed, degrading: %s", exc)
        return []


def _merge(per_source: list[list[SearchHit]]) -> list[MergedHit]:
    """Dedup hits across sources by DOI / ISBN-13 / arXiv id, then RRF-score."""
    merged: list[MergedHit] = []
    by_doi: dict[str, int] = {}
    by_isbn: dict[str, int] = {}
    by_arxiv: dict[str, int] = {}

    for hits in per_source:
        for hit in hits:
            existing_idx = _find_existing(hit, by_doi, by_isbn, by_arxiv)
            if existing_idx is None:
                merged.append(_seed(hit))
                idx = len(merged) - 1
            else:
                idx = existing_idx
                merged[idx] = _absorb(merged[idx], hit)
            _index(merged[idx], idx, by_doi, by_isbn, by_arxiv)

    return merged


def _find_existing(
    hit: SearchHit,
    by_doi: dict[str, int],
    by_isbn: dict[str, int],
    by_arxiv: dict[str, int],
) -> int | None:
    if hit.doi and hit.doi in by_doi:
        return by_doi[hit.doi]
    if hit.isbn_13 and hit.isbn_13 in by_isbn:
        return by_isbn[hit.isbn_13]
    if hit.arxiv_id and hit.arxiv_id in by_arxiv:
        return by_arxiv[hit.arxiv_id]
    return None


def _index(
    merged: MergedHit,
    idx: int,
    by_doi: dict[str, int],
    by_isbn: dict[str, int],
    by_arxiv: dict[str, int],
) -> None:
    if merged.doi:
        by_doi[merged.doi] = idx
    if merged.isbn_13:
        by_isbn[merged.isbn_13] = idx
    if merged.arxiv_id:
        by_arxiv[merged.arxiv_id] = idx


def _seed(hit: SearchHit) -> MergedHit:
    """Create a fresh MergedHit from a single SearchHit."""
    return MergedHit(
        title=hit.title,
        authors=list(hit.authors),
        year=hit.year,
        type=hit.type,
        doi=hit.doi,
        isbn_13=hit.isbn_13,
        isbn_10=hit.isbn_10,
        arxiv_id=hit.arxiv_id,
        sources=[hit.source],
        source_ids={hit.source: hit.source_id} if hit.source_id else {},
        score=_rrf_term(hit.raw_rank),
    )


def _absorb(merged: MergedHit, hit: SearchHit) -> MergedHit:
    """Fold a new hit into an existing MergedHit, updating score and ids."""
    sources = list(merged.sources)
    if hit.source not in sources:
        sources.append(hit.source)
    source_ids = dict(merged.source_ids)
    if hit.source_id:
        source_ids.setdefault(hit.source, hit.source_id)

    authors = merged.authors if len(merged.authors) >= len(hit.authors) else list(hit.authors)
    title = merged.title or hit.title

    new_type: HitType = merged.type
    if merged.type == "unknown":
        new_type = hit.type
    elif hit.type != "unknown" and hit.type != merged.type:
        # book/article disagreement — fall back to unknown rather than picking
        new_type = "unknown"

    year = merged.year if merged.year is not None else hit.year

    return replace(
        merged,
        title=title,
        authors=_dedupe_authors(authors),
        year=year,
        type=new_type,
        doi=merged.doi or hit.doi,
        isbn_13=merged.isbn_13 or hit.isbn_13,
        isbn_10=merged.isbn_10 or hit.isbn_10,
        arxiv_id=merged.arxiv_id or hit.arxiv_id,
        sources=sources,
        source_ids=source_ids,
        score=merged.score + _rrf_term(hit.raw_rank),
    )


def _dedupe_authors(authors: list[Author]) -> list[Author]:
    """Drop duplicate-name authors while preserving order."""
    seen: set[str] = set()
    result: list[Author] = []
    for author in authors:
        key = author.name.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(author)
    return result


def _rrf_term(rank: int) -> float:
    """Single-source contribution to the RRF score."""
    return 1.0 / (_RRF_K + rank)


def _sort_key(hit: MergedHit) -> tuple[float, int, int, str]:
    """Sort key — ascending sort yields score desc, sources desc, year desc, title asc."""
    return (-hit.score, -len(hit.sources), -(hit.year or 0), hit.title.lower())
