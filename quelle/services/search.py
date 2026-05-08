"""Search orchestrator.

Fans out a single query to every wired source's `search()` adapter in
parallel, then dedups and ranks the combined results with Reciprocal
Rank Fusion (RRF). Failures from individual sources are caught and
logged — the search degrades to whatever sources did respond rather
than failing the whole call.

This module is the single place that knows the source set and the
fusion logic. Adapters stay ignorant of one another; the resolver
(used by `quelle fetch`) delegates here for typed/authored free-text
queries.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import httpx

from quelle.models.publication import Author, Publication
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

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

SearchType = Literal["book", "article", "all"]

# Maps adapter slug -> (search callable, set of types the source covers,
# documented per-page upper bound on a single request). The cap is taken
# from each upstream's published API docs and used by `_per_source_limit`
# to clip an over-large `--limit`.
SOURCES: dict[str, tuple[Callable[..., list[SearchHit]], set[HitType], int]] = {
    openalex.SOURCE_NAME: (openalex.search, {"article", "book"}, 200),
    semantic_scholar.SOURCE_NAME: (semantic_scholar.search, {"article"}, 100),
    arxiv.SOURCE_NAME: (arxiv.search, {"article"}, 200),
    open_library.SOURCE_NAME: (open_library.search, {"book"}, 100),
    google_books.SOURCE_NAME: (google_books.search, {"book"}, 40),
    bnf.SOURCE_NAME: (bnf.search, {"book"}, 100),
}

_RRF_K = 60.0

# Floor for per-source pulls: with `--limit 1` we still ask each source
# for at least this many candidates so RRF has material to merge.
_PER_SOURCE_FLOOR = 20


def search(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    author: str | None = None,
    type: SearchType = "all",
    sources: list[str] | None = None,
    limit: int = 3,
) -> list[MergedHit]:
    """Run a multi-source search and return up to `limit` merged hits.

    Source set is selected from `SOURCES` by `type`, then narrowed by
    `sources` (allowlist) if provided. Per-source pull size is
    `max(limit * 2, 20)` clipped to each source's documented cap, so
    RRF has material to merge after dedup without exceeding any
    upstream's `per-page` limit.
    """
    if limit < 1:
        raise UserError("--limit must be at least 1")

    selected = _select_sources(type, sources)
    base_pull = max(limit * 2, _PER_SOURCE_FLOOR)

    kind_hint = type if type in {"book", "article"} else None
    raw: list[list[SearchHit]] = []
    with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as pool:
        futures = {
            pool.submit(
                _safe_call,
                fn,
                client,
                settings,
                query,
                author,
                kind_hint,
                _per_source_limit(name, base_pull),
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


def _per_source_limit(name: str, base_pull: int) -> int:
    """Clip the per-source pull to the source's documented per-request cap."""
    entry = SOURCES.get(name)
    if entry is None:
        return base_pull
    return min(base_pull, entry[2])


def resolve_top_hit(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    type_hint: str | None,
    author: str | None,
) -> Publication:
    """Pick the top multi-source hit for a typed/authored query and resolve it.

    If the top `MergedHit` already carries enough fields to satisfy the
    caller — title plus an id and at least year/kind/authors — we
    synthesise a `Publication` directly and skip the round-trip back
    through the id-keyed resolver. That avoids calling Open Library or
    OpenAlex twice for the same record. Otherwise we recurse into the
    regular id-based resolvers via `_resolve_top_hit_by_id` so the
    returned `Publication` is fully populated.
    """
    # Imported lazily to break the resolver/search cycle: `resolver.py`
    # imports `services.search.search`, and we call back into resolver-
    # internal helpers from here. Top-level imports would cycle.
    from quelle.services.resolver import resolve_book_primary

    type_value: SearchType = type_hint if type_hint in {"book", "article"} else "all"  # type: ignore[assignment]
    hits = search(
        client,
        settings,
        query,
        author=author,
        type=type_value,
        limit=1,
    )
    if not hits:
        from quelle.repositories.errors import NotFoundError

        raise NotFoundError(f"no match for {query!r} (type={type_value}, author={author!r})")
    top = hits[0]

    if _hit_is_self_sufficient(top):
        return _publication_from_merged_hit(top)

    if top.doi:
        return openalex.fetch_by_doi(client, settings, top.doi)
    if top.isbn_13 or top.isbn_10:
        return resolve_book_primary(client, settings, top.isbn_13 or top.isbn_10)
    if top.arxiv_id:
        return arxiv.fetch_by_arxiv_id(client, settings, top.arxiv_id)
    return _publication_from_merged_hit(top)


def _hit_is_self_sufficient(hit: MergedHit) -> bool:
    """True when a `MergedHit` carries enough to skip the id round-trip.

    Heuristic: title, year, a kind tag, at least one author, and was
    surfaced by 2+ sources (corroboration). If any of those are
    missing, the round-trip back into the id-based resolver buys us
    abstract / venue / publisher / citation count that the search
    layer doesn't fetch.
    """
    return bool(
        hit.title
        and hit.year is not None
        and hit.type in {"book", "article"}
        and hit.authors
        and len(hit.sources) >= 2
    )


def _publication_from_merged_hit(hit: MergedHit) -> Publication:
    """Synthesise a `Publication` from a search hit.

    Used both when the top hit lacks a fetchable identifier and when
    the hit is rich enough to skip the id-based round-trip. Fields not
    carried by `SearchHit` (abstract, citation count, OA flag, PDF
    URL, venue) stay `None` / `[]`.
    """
    kind = hit.type if hit.type in {"book", "article"} else None
    authors = [Author(name=a.name, orcid=a.orcid, affiliation=a.affiliation) for a in hit.authors]
    return Publication(
        title=hit.title,
        authors=authors,
        year=hit.year,
        kind=kind,
        doi=hit.doi,
        isbn_10=hit.isbn_10,
        isbn_13=hit.isbn_13,
        arxiv_id=hit.arxiv_id,
        resolved_from_chain=list(hit.sources),
    )


def _select_sources(
    type: SearchType,
    sources: list[str] | None,
) -> dict[str, Callable[..., list[SearchHit]]]:
    """Pick the source set for this run, validating any user filter."""
    allow = {name.strip() for name in sources or [] if name.strip()}
    unknown = allow - SOURCES.keys()
    if unknown:
        raise UserError(f"unknown source(s): {', '.join(sorted(unknown))}")

    selected: dict[str, Callable[..., list[SearchHit]]] = {}
    for name, (fn, covers, _cap) in SOURCES.items():
        if type != "all" and type not in covers:
            continue
        if allow and name not in allow:
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
                merged[idx] = _absorb_merged(merged[idx], _seed(hit))
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
    """Lowercase, drop diacritics + punctuation, collapse whitespace.

    For Latin-script titles (incl. accented French / Spanish / German)
    this folds e.g. *L'Étranger* and *L'Etranger* to the same key. For
    non-Latin scripts (Cyrillic, Arabic, CJK) the ASCII-drop reduces
    the title to an empty string; `_similarity_key` then returns
    `None` and the hit is preserved separately rather than collapsed
    against an unrelated empty-keyed neighbour.
    """
    folded = unicodedata.normalize("NFKD", title)
    stripped = folded.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in stripped).split())


# Lowercase particles that cling to a surname instead of being dropped.
# Covers French / Spanish / Portuguese / Dutch / German / Arabic patterns
# observed in BnF and OpenAlex author strings. Matched after lowercasing.
_SURNAME_PARTICLES: frozenset[str] = frozenset(
    {
        "de",
        "del",
        "della",
        "di",
        "da",
        "das",
        "dos",
        "du",
        "la",
        "le",
        "les",
        "van",
        "von",
        "der",
        "den",
        "ten",
        "ter",
        "af",
        "al",
        "el",
        "bin",
        "ibn",
        "abu",
        "abd",
        "saint",
        "st",
    }
)


def _surname(name: str) -> str:
    """Extract the surname from a name; handle multi-particle forms.

    Both `Last, First` (BnF, library catalogues) and `First Last`
    (OpenAlex, Google Books) shapes are supported. Multi-particle
    surnames such as `de la Vega`, `van der Berg`, `bin Salman` are
    recognised: when the trailing surname tokens contain a known
    particle (`de`, `la`, `van`, `der`, `bin`, ...), the full
    particle-prefixed surname is preserved rather than reduced to its
    last token alone.
    """
    if not name:
        return ""
    stripped = name.strip()
    if "," in stripped:
        last_part = stripped.split(",", 1)[0].strip()
    else:
        last_part = _trailing_surname(stripped)
    folded = unicodedata.normalize("NFKD", last_part)
    return folded.encode("ascii", "ignore").decode("ascii").lower().strip(",.;-")


def _trailing_surname(full_name: str) -> str:
    """Walk a `First Last` name from the right and pull in particles.

    Stops at the first non-particle token from the right. Returns
    every token from that boundary onward, joined with spaces. For
    `Ana de la Vega` the boundary is `Vega`; the walk reads `la` and
    `de` as particles and returns `de la Vega`. For `Ada Lovelace`
    the walk reads `Lovelace` and stops, returning `Lovelace`.
    """
    tokens = full_name.split()
    if not tokens:
        return ""
    boundary = len(tokens) - 1
    while boundary > 0 and tokens[boundary - 1].lower().rstrip(".") in _SURNAME_PARTICLES:
        boundary -= 1
    return " ".join(tokens[boundary:])


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
