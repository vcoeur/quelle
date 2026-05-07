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
from dataclasses import replace

import httpx

from quelle._isbn import isbn10_to_13, isbn13_to_10
from quelle.models.publication import Author, Publication
from quelle.models.search import MergedHit
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

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5}(v\d+)?|[a-z\-]+/\d{7}(v\d+)?)$", re.IGNORECASE)
_SCHOLAR_HOST_RE = re.compile(r"scholar\.google\.\w+", re.IGNORECASE)
_ISBN_DIGITS_RE = re.compile(r"^[0-9]{9}[0-9X]$|^97[89][0-9]{10}$")


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
    multi-source search service and the top hit is recursively
    resolved via its DOI / ISBN / arXiv id. Explicit identifier
    queries (DOI, ISBN, arXiv id) ignore both hints and resolve
    directly.
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
        return _resolve_book_primary(client, settings, isbn)

    doi_candidate = _extract_doi(stripped)
    if doi_candidate:
        return openalex.fetch_by_doi(client, settings, doi_candidate)

    if _ARXIV_RE.match(stripped):
        return arxiv.fetch_by_arxiv_id(client, settings, stripped)

    if type_hint is not None or author is not None:
        return _resolve_via_search(client, settings, stripped, type_hint, author)

    return openalex.search_by_title(client, settings, stripped)


def _resolve_via_search(
    client: httpx.Client,
    settings: Settings,
    query: str,
    type_hint: str | None,
    author: str | None,
) -> Publication:
    """Pick the top multi-source hit for a typed/authored query and resolve it.

    If the top hit carries any of DOI / ISBN-13 / ISBN-10 / arXiv id,
    we recurse into the regular id-based resolvers so the returned
    Publication is fully populated. If the hit only has a source-native
    id (e.g. an Open Library Work key), we synthesise a minimal
    Publication from the SearchHit fields — the user gets back what
    the search saw, just less rich than a DOI/ISBN-anchored fetch.
    """
    from quelle.services.search import search as multi_search

    type_value = type_hint or "all"
    hits = multi_search(
        client,
        settings,
        query,
        author=author,
        type=type_value,  # type: ignore[arg-type]
        limit=1,
    )
    if not hits:
        raise NotFoundError(f"no match for {query!r} (type={type_value}, author={author!r})")
    top = hits[0]
    if top.doi:
        return openalex.fetch_by_doi(client, settings, top.doi)
    if top.isbn_13 or top.isbn_10:
        return _resolve_book_primary(client, settings, top.isbn_13 or top.isbn_10)
    if top.arxiv_id:
        return arxiv.fetch_by_arxiv_id(client, settings, top.arxiv_id)
    return _publication_from_merged_hit(top)


def _publication_from_merged_hit(hit: MergedHit) -> Publication:
    """Synthesise a Publication from a search hit with no fetchable identifier.

    Fields not carried by SearchHit (abstract, citation count, OA flag,
    PDF URL, venue) stay `None` / `[]`. The returned record is
    intentionally shallow — the user should pass it through enrichment
    or copy the title to a richer source if more is needed.
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


def _resolve_book_primary(client: httpx.Client, settings: Settings, isbn: str) -> Publication:
    """Try the book sources in priority order and return the first hit.

    Order: Open Library (broad ISBN coverage) → Google Books (broad
    fallback) → BnF (strong on French) → OpenAlex (last resort, prone
    to false positives because OpenAlex doesn't index books by ISBN
    natively). See `openalex.fetch_by_isbn` for the caveat.
    """
    last_error: PublicationsError | None = None
    for source_call in (
        lambda: open_library.fetch_by_isbn(client, settings, isbn),
        lambda: google_books.fetch_by_isbn(client, settings, isbn),
        lambda: bnf.fetch_by_isbn(client, settings, isbn),
        lambda: openalex.fetch_by_isbn(client, settings, isbn),
    ):
        try:
            return source_call()
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
        hit = _lookup_in_cache(cache, query, type_hint=type_hint, author=author)
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
    return current


def _enrich_book(
    client: httpx.Client,
    settings: Settings,
    current: Publication,
) -> Publication:
    """Fill missing book fields from the other book sources, in priority order.

    Stops as soon as the record is "complete enough" — has a publisher,
    a year, and either a description (abstract) or subjects. Each
    source call is best-effort and swallowed on failure.
    """
    isbn = current.isbn_13 or current.isbn_10
    if not isbn:
        return current

    head = current.resolved_from_chain[0] if current.resolved_from_chain else None
    fallback_calls: list[tuple[str, callable]] = [
        ("open_library", lambda: open_library.fetch_by_isbn(client, settings, isbn)),
        ("google_books", lambda: google_books.fetch_by_isbn(client, settings, isbn)),
        ("bnf", lambda: bnf.fetch_by_isbn(client, settings, isbn)),
        ("openalex", lambda: openalex.fetch_by_isbn(client, settings, isbn)),
    ]

    for source_name, call in fallback_calls:
        if source_name == head:
            continue
        if _book_record_complete(current):
            break
        current = _try_enrich(current, call)
    return current


def _book_record_complete(record: Publication) -> bool:
    """Heuristic for when to stop enriching a book record."""
    return bool(record.publisher and record.year and (record.abstract or record.subjects))


def _lookup_in_cache(
    cache: Cache,
    query: str,
    *,
    type_hint: str | None = None,
    author: str | None = None,
) -> Publication | None:
    """Try every cache lookup route for the given query string.

    Identifier-based lookups (DOI / ISBN / arXiv id / OpenAlex id) are
    always honoured — those are unambiguous. The title-fallback is
    skipped when `type_hint` or `author` is set, since a cached entry
    keyed by the exact title may have been resolved without that hint
    and would short-circuit the explicit disambiguation.
    """
    stripped = query.strip()
    isbn = _extract_isbn(stripped)
    if isbn:
        hit = cache.get_by_isbn(isbn)
        if hit is not None:
            return hit
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
    if type_hint is not None or author is not None:
        return None
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


def _extract_isbn(query: str) -> str | None:
    """Pull a bare ISBN out of `ISBN: ...`, hyphenated, or plain digit strings.

    Accepts ISBN-10 (9 digits + check, where check may be `X`) and
    ISBN-13 (978/979 + 10 digits). Hyphens and spaces are stripped
    before validating; the returned form is digits-only (with `X`
    preserved on ISBN-10).
    """
    raw = query.strip().lower()
    raw = raw.removeprefix("isbn:")
    raw = raw.removeprefix("isbn ")
    digits = "".join(ch for ch in raw if ch.isdigit() or ch in "xX").upper()
    if not digits:
        return None
    if _ISBN_DIGITS_RE.match(digits):
        return digits
    return None
