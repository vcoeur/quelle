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
import unicodedata
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
    limit: int = 3,
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

    kind_hint = type if type in {"book", "article"} else None
    raw: list[list[SearchHit]] = []
    with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as pool:
        futures = {
            pool.submit(
                _safe_call, fn, client, settings, query, author, kind_hint, per_source_limit
            ): name
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
    merged = _dedup_by_similarity(merged)
    if kind_hint is not None:
        merged = [hit for hit in merged if hit.type in {kind_hint, "unknown"}]
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
    kind: str | None,
    limit: int,
) -> list[SearchHit]:
    """Invoke a source's `search()` and swallow expected errors.

    Every adapter accepts `kind` via its keyword-only signature; sources
    that don't expose a native type filter ignore it. Currently only
    OpenAlex (multi-type) uses it.
    """
    try:
        return fn(client, settings, query, author=author, kind=kind, limit=limit)
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


def _dedup_by_similarity(merged: list[MergedHit]) -> list[MergedHit]:
    """Second-pass merge for hits with no shared identifier.

    Groups by (normalised title, first-author surname). Within each
    group, folds every subsequent hit into the first one. Hits whose
    similarity key is incomplete (empty title or no authors) skip the
    grouping pass and survive untouched.

    This catches cases where two sources return the same publication
    but neither carries a DOI / ISBN / arXiv id (common for older
    books, French-language editions, or sources that omit external
    identifiers).
    """
    canonical: dict[tuple[str, str], int] = {}
    survivors: list[MergedHit] = []
    survived_indexes: list[int] = []

    for hit in merged:
        key = _similarity_key(hit)
        if key is None:
            survivors.append(hit)
            survived_indexes.append(len(survivors) - 1)
            continue
        existing = canonical.get(key)
        if existing is None:
            survivors.append(hit)
            canonical[key] = len(survivors) - 1
        else:
            survivors[existing] = _absorb_merged(survivors[existing], hit)

    return survivors


def _similarity_key(hit: MergedHit) -> tuple[str, str] | None:
    """Build a (normalised-title, first-author-surname) key, or None if incomplete."""
    title = _normalise_title(hit.title)
    if not title or not hit.authors:
        return None
    surname = _surname(hit.authors[0].name)
    if not surname:
        return None
    return title, surname


def _normalise_title(title: str) -> str:
    """Lowercase, drop diacritics + punctuation, collapse whitespace."""
    folded = unicodedata.normalize("NFKD", title)
    stripped = folded.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in stripped).split())


def _surname(name: str) -> str:
    """Extract the surname from a name; handle both `First Last` and `Last, First`.

    BnF and some library catalogues return authors as `Camus, Albert`,
    while OpenAlex / Google Books return `Albert Camus`. Both should
    fold to `camus`.
    """
    if not name:
        return ""
    stripped = name.strip()
    if "," in stripped:
        last = stripped.split(",", 1)[0].strip()
    else:
        tokens = stripped.split()
        last = tokens[-1] if tokens else ""
    folded = unicodedata.normalize("NFKD", last)
    return folded.encode("ascii", "ignore").decode("ascii").lower().strip(",.;")


def _absorb_merged(base: MergedHit, other: MergedHit) -> MergedHit:
    """Fold one MergedHit into another, combining sources and scores."""
    sources = list(base.sources)
    for src in other.sources:
        if src not in sources:
            sources.append(src)
    source_ids = dict(base.source_ids)
    for src, sid in other.source_ids.items():
        source_ids.setdefault(src, sid)

    authors = base.authors if len(base.authors) >= len(other.authors) else list(other.authors)
    title = base.title or other.title

    new_type: HitType = base.type
    if base.type == "unknown":
        new_type = other.type
    elif other.type != "unknown" and other.type != base.type:
        new_type = "unknown"

    year = base.year if base.year is not None else other.year

    return replace(
        base,
        title=title,
        authors=_dedupe_authors(authors),
        year=year,
        type=new_type,
        doi=base.doi or other.doi,
        isbn_13=base.isbn_13 or other.isbn_13,
        isbn_10=base.isbn_10 or other.isbn_10,
        arxiv_id=base.arxiv_id or other.arxiv_id,
        sources=sources,
        source_ids=source_ids,
        score=base.score + other.score,
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
