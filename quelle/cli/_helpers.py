"""CLI-internal helpers — heuristics, error reporting, dataclass flatteners.

Kept separate from `cli/main.py` so the Typer command bodies stay short and
the helpers can be unit-tested without invoking the CLI runner.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict

from quelle.models.publication import Publication
from quelle.models.search import MergedHit
from quelle.repositories.errors import (
    CacheError,
    ConfigError,
    NetworkError,
    NotFoundError,
    PublicationsError,
    UserError,
)


def resolve_type_hint(book: bool, article: bool) -> str | None:
    """Translate the mutually-exclusive `--book` / `--article` flags into a hint.

    Both absent → `None` (every source covered by the caller). Both
    present is a user error; the caller is expected to surface it.
    Pure logic, lifted out of `cli/main.py` for unit-testability without
    the Typer runner.
    """
    if book and article:
        raise UserError("--book and --article are mutually exclusive")
    if book:
        return "book"
    if article:
        return "article"
    return None


def looks_like_explicit_id(query: str) -> bool:
    """Cheap check: does the query look like a DOI, ISBN, or arXiv id?

    Used to suppress the comma-split heuristic on `quelle fetch` for
    explicit-id queries, since those occasionally contain commas
    (DOIs especially) and have no need for an author hint.
    """
    stripped = query.strip().lower()
    if stripped.startswith(("doi:", "isbn:", "isbn ", "https://doi.org/", "http://doi.org/")):
        return True
    if re.match(r"^10\.\d{4,9}/\S+$", stripped):
        return True
    isbn_chars = "".join(ch for ch in stripped if not ch.isspace() and ch != "-")
    if len(isbn_chars) in (10, 13) and isbn_chars.replace("x", "").isdigit():
        return True
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", stripped):
        return True
    return bool(re.match(r"^[a-z\-]+/\d{7}(v\d+)?$", stripped))


def split_author_from_query(query: str) -> tuple[str, str | None]:
    """Heuristic split of `"<title>, <author>"` into separate parts.

    Returns the original query unchanged unless the trailing piece
    after the last comma is a plausible single-name author (1-3
    tokens, no digits). Designed so that titles legitimately
    containing commas still survive when they end with substantive
    content (e.g. `"Pride and Prejudice"` is unchanged), while a
    `"title, surname"` shape is split.
    """
    last_comma = query.rfind(",")
    if last_comma < 0:
        return query, None
    title = query[:last_comma].strip()
    author = query[last_comma + 1 :].strip()
    if not title or not author:
        return query, None
    tokens = author.split()
    if not 1 <= len(tokens) <= 3:
        return query, None
    if any(any(ch.isdigit() for ch in token) for token in tokens):
        return query, None
    return title, author


def publication_to_dict(publication: Publication) -> dict:
    """Flatten a Publication dataclass into a JSON-serialisable dict."""
    data = asdict(publication)
    data["citation_key"] = publication.citation_key()
    return data


def hit_to_dict(rank: int, hit: MergedHit) -> dict:
    """Flatten a MergedHit dataclass for output rendering."""
    pref = hit.preferred_id()
    if pref is None:
        id_str: str | None = None
        resolvable = False
    else:
        kind, value = pref
        id_str = f"{kind}:{value}"
        resolvable = kind in {"doi", "isbn", "arxiv"}
    return {
        "rank": rank,
        "title": hit.title,
        "authors": [asdict(a) for a in hit.authors],
        "year": hit.year,
        "type": hit.type,
        "id": id_str,
        "id_resolvable": resolvable,
        "ids": {
            "doi": hit.doi,
            "isbn_13": hit.isbn_13,
            "isbn_10": hit.isbn_10,
            "arxiv_id": hit.arxiv_id,
        },
        "sources": list(hit.sources),
        "source_ids": dict(hit.source_ids),
        "score": round(hit.score, 6),
    }


_ERROR_HINTS: dict[type, str] = {
    NotFoundError: (
        "Try a different identifier, or check OpenAlex directly at "
        "https://api.openalex.org/works?search=<title>"
    ),
    NetworkError: (
        "Network or upstream API failure. Retry in a moment; if it persists, "
        "pass --no-cache to bypass any stale lookup and re-run."
    ),
    ConfigError: (
        "Configuration is missing or incomplete. See `.env.example` for the "
        "full list of variables and copy it to `.env`."
    ),
    CacheError: (
        "Local cache failure — the SQLite file may be corrupt. Try `quelle cache clear --yes`."
    ),
    UserError: "Invalid input — `quelle fetch --help` for usage.",
}


def exit_code_for(exc: PublicationsError) -> int:
    """Map a structured error to a CLI exit code."""
    if isinstance(exc, (UserError, NotFoundError)):
        return 1
    if isinstance(exc, NetworkError):
        return 2
    if isinstance(exc, CacheError):
        return 3
    if isinstance(exc, ConfigError):
        return 4
    return 1


def report_error(exc: PublicationsError) -> None:
    """Write a structured error + hint to stderr."""
    sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
    hint = _ERROR_HINTS.get(type(exc))
    if hint is None:
        for base, text in _ERROR_HINTS.items():
            if isinstance(exc, base):
                hint = text
                break
    if hint:
        sys.stderr.write(f"  -> {hint}\n")
