"""CLI-internal helpers — heuristics, error reporting, dataclass flatteners.

Kept separate from `cli/main.py` so the Typer command bodies stay short and
the helpers can be unit-tested without invoking the CLI runner.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from quelle.models.publication import Author, Publication
from quelle.models.search import MergedHit
from quelle.repositories.errors import (
    CacheError,
    ConfigError,
    NetworkError,
    NotFoundError,
    PublicationsError,
    UserError,
)
from quelle.services.citekey import base_key, vault_kind
from quelle.settings import Settings, load_settings

# Exit code for CLI usage errors (bad flags / missing arguments), per BSD
# `EX_USAGE`. Click defaults usage errors to exit 2, which collides with
# the documented "network error" code; `cli/main.py` repoints click's
# `UsageError.exit_code` at this constant once at import time.
EX_USAGE = 64

# quelle `kind` → CSL-JSON item type. Export-only; the canonical
# convention rules live in `quelle.services.citekey`.
_CSL_TYPE: dict[str, str] = {
    "article": "article-journal",
    "preprint": "article",
    "book": "book",
    "book-chapter": "chapter",
    "web": "webpage",
    "media": "motion_picture",
}


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


def publication_to_dict(publication: Publication, *, citekey: str | None = None) -> dict:
    """Flatten a Publication into the canonical Source dict.

    `citation_key` stays the BibTeX-style key (`Publication.citation_key()`).
    A top-level `x_vcoeur` block carries the vault-ready CiteKey: `citekey`
    is the minted (collision-resolved) key when one is passed, else the
    un-disambiguated `base_key`. `vault_id` / `confidence` are placeholders
    knoten fills on ingest; `vault_kind` is the quelle→knoten kind map.
    """
    data = asdict(publication)
    data["citation_key"] = publication.citation_key()
    data["x_vcoeur"] = {
        "citekey": citekey or base_key(publication),
        "vault_id": None,
        "vault_kind": vault_kind(publication.kind),
        "confidence": None,
    }
    return data


def publication_to_csl(publication: Publication, *, citekey: str | None = None) -> dict:
    """Render a Publication as a single CSL-JSON item (export only).

    `id` is the CiteKey (`citekey` override or `base_key`); `type` maps
    the quelle kind to a CSL item type; authors become `[{family, given}]`;
    the year becomes `issued.date-parts`. DOI / ISBN / URL / container are
    included when present.
    """
    csl_type = _CSL_TYPE.get(publication.kind or "", "document")
    entry: dict = {
        "id": citekey or base_key(publication),
        "type": csl_type,
        "title": publication.title,
    }
    if publication.authors:
        entry["author"] = [_csl_name(author) for author in publication.authors]
    if publication.year:
        entry["issued"] = {"date-parts": [[publication.year]]}
    container = publication.venue or publication.publisher
    if container:
        entry["container-title"] = container
    if publication.doi:
        entry["DOI"] = publication.doi
    isbn = publication.isbn_13 or publication.isbn_10
    if isbn:
        entry["ISBN"] = isbn
    url = publication.source_url or publication.pdf_url
    if url:
        entry["URL"] = url
    return entry


def _csl_name(author: Author) -> dict:
    """Split an author's display name into CSL `{family, given}`.

    Last whitespace-separated token is the family name, the remainder the
    given name. A single-token name yields just `family`.
    """
    tokens = author.name.split()
    if not tokens:
        return {"family": author.name}
    if len(tokens) == 1:
        return {"family": tokens[0]}
    return {"family": tokens[-1], "given": " ".join(tokens[:-1])}


def load_taken_set(taken_csv: str | None, taken_file: str | None) -> set[str]:
    """Build the taken-CiteKey set from `--taken` and `--taken-file`.

    `--taken` is a comma-separated list. `--taken-file` is a path (or `-`
    for stdin) holding either newline-delimited CiteKeys or the JSON
    object emitted by `knoten citekeys --json` (`{"citekeys": [...]}`,
    detected by a leading `{`). The two sources are unioned.
    """
    taken: set[str] = set()
    if taken_csv:
        for key in taken_csv.split(","):
            cleaned = key.strip()
            if cleaned:
                taken.add(cleaned)
    if taken_file:
        text = sys.stdin.read() if taken_file == "-" else Path(taken_file).read_text("utf-8")
        taken |= _parse_taken_text(text)
    return taken


def _parse_taken_text(text: str) -> set[str]:
    """Parse a taken-file body: JSON `{citekeys:[...]}`, a JSON array of
    strings, or one key per line.

    Raises ValueError when the JSON does not carry a list of strings — a
    string `citekeys` value especially must not be iterated into single
    characters, which would silently weaken minting.
    """
    stripped = text.strip()
    if not stripped:
        return set()
    if stripped.startswith(("{", "[")):
        obj = json.loads(stripped)
        keys = obj.get("citekeys", []) if isinstance(obj, dict) else obj
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError(
                'taken-set JSON must be a list of strings, or {"citekeys": [<strings>]}'
            )
        return {key.strip() for key in keys if key.strip()}
    return {line.strip() for line in stripped.splitlines() if line.strip()}


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


def load_settings_or_exit() -> Settings:
    """Load settings, mapping a `ConfigError` (e.g. a malformed `.env`
    value) to a reported error + the documented exit code 4 instead of a
    traceback."""
    try:
        return load_settings()
    except PublicationsError as exc:
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc


def exit_code_for(exc: PublicationsError) -> int:
    """Map a structured error to a CLI exit code.

    Usage errors (bad flags / arguments) are click's domain and exit with
    `EX_USAGE` (64); they never reach this classifier.
    """
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
