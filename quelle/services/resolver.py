"""Resolution chain — turn user input into a normalised `Publication`.

Two public entry points:

- `resolve(client, settings, query)` — single-source resolution.
  Picks the right source based on the shape of the query and returns
  whatever it finds.
- `resolve_with_enrichment(client, settings, query)` — runs
  `resolve` and then fills missing fields from secondary sources
  (Crossref, Semantic Scholar) when possible. This is what the CLI
  `fetch` command uses.

The book-source priority chain is the single tuple `BOOK_SOURCES` —
both the primary fallback walk (`resolve_book_primary`) and the
enrichment loop (`_enrich_book`) iterate it, so the order is in one
place and stays consistent.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import httpx

from quelle._identifiers import ARXIV_ID_RE as _ARXIV_RE
from quelle._identifiers import extract_doi as _extract_doi
from quelle._identifiers import extract_isbn as _extract_isbn
from quelle._isbn import isbn10_to_13, isbn13_to_10
from quelle.models.publication import Publication
from quelle.repositories.cache import Cache
from quelle.repositories.errors import NetworkError, NotFoundError, PublicationsError, UserError
from quelle.repositories.sources import (
    arxiv,
    bnf,
    crossref,
    google_books,
    open_library,
    openalex,
    semantic_scholar,
)
from quelle.settings import Settings

_SCHOLAR_HOST_RE = re.compile(r"scholar\.google\.\w+", re.IGNORECASE)

# Identifiers embedded anywhere in a URL — used to route a landing page
# (a DOI resolver link, an arXiv abs/pdf page) back to the rich resolver.
# Bare-identifier shapes live in `quelle._identifiers`; only the
# URL-embedding patterns are a resolver concern.
_DOI_IN_URL_RE = re.compile(r"10\.\d{4,9}/[^\s?#]+")
_ARXIV_IN_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/((?:[a-z\-]+(?:\.[a-z]{2})?/)?[^\s?#/]+?)(?:\.pdf)?/?$",
    re.IGNORECASE,
)

# Obvious file-extension suffixes that publisher URLs append after the DOI
# (`…/10.1101/2020.01.01.123456v1.full.pdf`) — never part of the DOI itself.
_DOI_TRAILING_EXT_RE = re.compile(r"\.(?:pdf|html?|xml|txt|full|abstract)$", re.IGNORECASE)

# Minimum SequenceMatcher ratio for two normalised titles to count as the
# same work during enrichment.
_TITLE_MATCH_RATIO = 0.85


# Single source of truth for the book-source priority chain. The
# `fetch_by_isbn` callable is captured via lambda so we can swap the
# bound module in tests via `monkeypatch.setattr`.
#
# Order: Open Library (broad ISBN coverage) → Google Books (broad
# fallback) → BnF (strong on French) → OpenAlex (last resort, prone
# to false positives because OpenAlex doesn't index books by ISBN
# natively). See `openalex.fetch_by_isbn` for the caveat.
def _book_sources() -> tuple[tuple[str, Callable[[httpx.Client, Settings, str], Publication]], ...]:
    return (
        ("open_library", open_library.fetch_by_isbn),
        ("google_books", google_books.fetch_by_isbn),
        ("bnf", bnf.fetch_by_isbn),
        ("openalex", openalex.fetch_by_isbn),
    )


def resolve(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    type_hint: str | None = None,
    author: str | None = None,
) -> Publication:
    """Route a query to a single source and return its Publication.

    `type_hint` (`book` or `article`) and `author` only affect the
    free-text path: when either is set, the query is run through the
    multi-source search service via `resolve_top_hit` and either
    synthesised from the merged hit or recursively resolved by id.
    Explicit identifier queries (DOI, ISBN, arXiv id) ignore both
    hints and resolve directly.
    """
    stripped = query.strip()

    if _SCHOLAR_HOST_RE.search(stripped):
        # Google Scholar has no public API and its ToS prohibits scraping.
        # We do not resolve Scholar URLs — the user should open the page,
        # copy the paper title, and retry with the title as a free-text query.
        raise UserError(
            "Google Scholar URLs are not supported. Open the Scholar page, "
            "copy the paper title, and retry: "
            'publications fetch "<paper title>"'
        )

    isbn = _extract_isbn(stripped)
    if isbn:
        return resolve_book_primary(client, settings, isbn)

    doi_candidate = _extract_doi(stripped)
    if doi_candidate:
        return openalex.fetch_by_doi(client, settings, doi_candidate)

    if _ARXIV_RE.match(stripped):
        return arxiv.fetch_by_arxiv_id(client, settings, stripped)

    if type_hint is not None or author is not None:
        from quelle.services.search import resolve_top_hit

        return resolve_top_hit(client, settings, stripped, type_hint=type_hint, author=author)

    return openalex.search_by_title(client, settings, stripped)


def resolve_any(
    client: httpx.Client,
    settings: Settings,
    raw_input: str,
    *,
    cache: Cache | None = None,
    type_hint: str | None = None,
    author: str | None = None,
) -> Publication:
    """Universal entry: turn *any* input into a Publication.

    Routes by input shape, in order:

    1. an existing local `.pdf` path → the PDF resolver;
    2. an http(s) URL → if it embeds a DOI / arXiv id, the rich
       resolver; otherwise the generic URL (web/media) resolver;
    3. an explicit DOI / ISBN / arXiv id, or free text → the existing
       `resolve_with_enrichment` chain.

    `type_hint` / `author` only steer the free-text path (same contract
    as `resolve`). Always returns a Publication.
    """
    stripped = raw_input.strip()

    if _looks_like_pdf_path(stripped):
        from quelle.services.pdf_resolver import resolve_local_pdf

        return resolve_local_pdf(Path(stripped))

    if _is_http_url(stripped):
        from quelle.services.url_resolver import resolve_url

        embedded = _embedded_identifier(stripped)
        if embedded is not None:
            try:
                return resolve_with_enrichment(client, settings, embedded, cache=cache)
            except NotFoundError:
                # The extracted id can over- or under-capture (version
                # suffixes, unregistered preprint DOIs) — degrade to the
                # Open-Graph resolver instead of failing the whole resolve.
                pass

        return resolve_url(client, settings, stripped)

    return resolve_with_enrichment(
        client,
        settings,
        stripped,
        cache=cache,
        type_hint=type_hint,
        author=author,
    )


def _looks_like_pdf_path(value: str) -> bool:
    """True when `value` is an existing local file ending in `.pdf`."""
    if not value.lower().endswith(".pdf"):
        return False
    try:
        return Path(value).is_file()
    except OSError:
        return False


def _is_http_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _embedded_identifier(url: str) -> str | None:
    """Extract a DOI or arXiv id embedded in a URL, or None.

    A `doi.org` landing page or any path-embedded DOI resolves to the
    bare DOI; an `arxiv.org/abs|pdf/<id>` page resolves to the arXiv id.
    """
    doi = _extract_doi(url)
    if doi:
        return doi
    match = _DOI_IN_URL_RE.search(url)
    if match:
        return _trim_doi_file_extensions(match.group(0).rstrip("/"))
    arxiv_match = _ARXIV_IN_URL_RE.search(url)
    if arxiv_match and _ARXIV_RE.match(arxiv_match.group(1)):
        return arxiv_match.group(1)
    return None


def _trim_doi_file_extensions(doi: str) -> str:
    """Strip stacked trailing file extensions (`.full.pdf`) off a captured DOI."""
    while True:
        trimmed = _DOI_TRAILING_EXT_RE.sub("", doi)
        if trimmed == doi:
            return doi
        doi = trimmed


def resolve_book_primary(client: httpx.Client, settings: Settings, isbn: str) -> Publication:
    """Try the book sources in priority order and return the first hit."""
    last_error: PublicationsError | None = None
    for _name, fetch in _book_sources():
        try:
            return fetch(client, settings, isbn)
        except (NotFoundError, NetworkError) as exc:
            last_error = exc
            continue
    raise last_error or NotFoundError(f"no book source matched ISBN: {isbn}")


def resolve_with_enrichment(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    cache: Cache | None = None,
    type_hint: str | None = None,
    author: str | None = None,
) -> Publication:
    """Resolve then enrich.

    If `cache` is provided, check it by DOI / OpenAlex id / arXiv id
    / exact title before calling any upstream source. On a miss, run
    the full chain and upsert the result. When `type_hint` or `author`
    is set, the title-based cache fallback is skipped — the user is
    explicitly disambiguating, and a stale title-keyed cache entry
    might have been resolved without the same hint.

    1. Run the primary resolver.
    2. If we started on arXiv and have no DOI, try OpenAlex title
       search to find a published version.
    3. If we have a DOI and an abstract or venue is missing, try
       Crossref.
    4. If the abstract is still missing and we have a DOI, try
       Semantic Scholar.
    """
    if cache is not None:
        hit = cache.lookup(query, type_hint=type_hint, author=author)
        if hit is not None:
            return hit

    primary = resolve(client, settings, query, type_hint=type_hint, author=author)
    current = primary

    if current.kind in {"book", "book-chapter"}:
        current = _enrich_book(client, settings, current)
        current = _backfill_isbn_pair(current)
    else:
        current = _enrich_article(client, settings, current)

    if cache is not None:
        cache.upsert(current)
    return current


def _backfill_isbn_pair(record: Publication) -> Publication:
    """Compute the missing ISBN form so cache lookups by either succeed.

    When a source returns only one of (ISBN-10, ISBN-13) we derive the
    other arithmetically. Both algorithms are deterministic and live
    in `_isbn10_to_13` / `_isbn13_to_10`. ISBN-13 outside the 978
    prefix has no ISBN-10 equivalent and is left untouched.
    """
    updates: dict[str, str] = {}
    if record.isbn_10 and not record.isbn_13:
        derived = isbn10_to_13(record.isbn_10)
        if derived:
            updates["isbn_13"] = derived
    if record.isbn_13 and not record.isbn_10:
        derived = isbn13_to_10(record.isbn_13)
        if derived:
            updates["isbn_10"] = derived
    return replace(record, **updates) if updates else record


def _enrich_article(
    client: httpx.Client,
    settings: Settings,
    current: Publication,
) -> Publication:
    """Article enrichment chain: arXiv→OpenAlex by title, then Crossref, then S2."""
    chain = current.resolved_from_chain
    started_on_arxiv = bool(chain) and chain[0] == "arxiv"
    if started_on_arxiv and not current.doi:
        # The title search returns its top hit unconditionally; gate the
        # merge on actual title similarity — a wrong hit's DOI is sticky
        # and would drive the Crossref / S2 enrichment off the wrong work.
        current = _try_enrich(
            current,
            lambda: openalex.search_by_title(client, settings, current.title),
            accept=lambda other: _titles_match(current.title, other.title),
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
    return current


def _enrich_book(
    client: httpx.Client,
    settings: Settings,
    current: Publication,
) -> Publication:
    """Fill missing book fields from the other book sources, in priority order.

    Stops as soon as the record is "complete enough" — has authors, a
    publisher, a year, and either a description (abstract) or subjects.
    Each source call is best-effort and swallowed on failure. The
    iteration order comes from `_book_sources` so the primary chain
    and the enrichment loop never disagree.
    """
    isbn = current.isbn_13 or current.isbn_10
    if not isbn:
        return current

    for source_name, fetch in _book_sources():
        # Skip every source already merged into the record — the chain
        # grows as the loop enriches, so check it live each iteration.
        if source_name in current.resolved_from_chain:
            continue
        if _book_record_complete(current):
            break
        current = _try_enrich(current, lambda f=fetch, i=isbn: f(client, settings, i))
    return current


def _book_record_complete(record: Publication) -> bool:
    """Heuristic for when to stop enriching a book record.

    Requires `authors` too: an authorless record forces the CiteKey down to a
    title-based key (e.g. `RadicalCandor2019`) instead of the author-based
    `Scott2019`, so keep walking the book sources until a name turns up — Open
    Library in particular often omits authors that Google Books carries.
    """
    return bool(
        record.authors and record.publisher and record.year and (record.abstract or record.subjects)
    )


def _try_enrich(
    current: Publication,
    fetch: Callable[[], Publication],
    *,
    accept: Callable[[Publication], bool] | None = None,
) -> Publication:
    """Run `fetch`, merge its result into `current`, swallow failures.

    When `accept` is given, the fetched record is merged only if the
    predicate approves it; otherwise `current` is returned unchanged.
    """
    try:
        other = fetch()
    except (NotFoundError, NetworkError, PublicationsError):
        return current
    if accept is not None and not accept(other):
        return current
    return current.merged_with(other)


def _titles_match(left: str, right: str) -> bool:
    """True when two titles plausibly name the same work.

    Casefolded, non-alphanumerics stripped; containment covers subtitle
    differences, the similarity ratio covers small wording drift.
    """
    a = _normalised_title(left)
    b = _normalised_title(right)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= _TITLE_MATCH_RATIO


def _normalised_title(title: str) -> str:
    return "".join(ch for ch in title.casefold() if ch.isalnum())
