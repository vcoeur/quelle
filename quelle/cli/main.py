"""Typer CLI entrypoint for the `quelle` command.

Each subcommand is a thin wrapper: parse flags, load Settings, open an
httpx client, call the resolver, render the result via
`quelle.cli.output`.

Exit codes (mapped from exception types in `quelle.repositories.errors`):
    0 success
    1 user error / not found
    2 network error / rate limit
    3 cache error
    4 config error
"""

from __future__ import annotations

import sys
from dataclasses import asdict

import typer

from quelle import __version__
from quelle.cli.config import config_app, init_command
from quelle.cli.output import (
    OutputMode,
    emit_json,
    render_cache_list,
    render_config,
    render_publication,
    render_search,
)
from quelle.models.publication import Publication
from quelle.models.search import MergedHit
from quelle.repositories.cache import Cache
from quelle.repositories.errors import (
    CacheError,
    ConfigError,
    NetworkError,
    NotFoundError,
    PublicationsError,
    UserError,
)
from quelle.repositories.http_client import build_client
from quelle.services import search as search_service
from quelle.services.resolver import resolve_with_enrichment
from quelle.settings import Settings, load_settings

app = typer.Typer(
    help="Fetch publication metadata and PDFs from open academic APIs.",
    no_args_is_help=True,
    add_completion=False,
)

cache_app = typer.Typer(help="Inspect the local SQLite cache.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")
app.add_typer(config_app, name="config")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Print version and exit.",
        is_eager=True,
    ),
) -> None:
    """Root callback — handles the global `--version` flag.

    `invoke_without_command=True` lets `quelle --version` short-circuit
    without requiring a subcommand; a bare `quelle` with no subcommand
    still falls through to the help view via `no_args_is_help=True`.
    """
    if version:
        typer.echo(f"quelle {__version__}")
        raise typer.Exit(0)


def _load() -> Settings:
    return load_settings()


def _exit_code(exc: PublicationsError) -> int:
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


def _report(exc: PublicationsError) -> None:
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


@app.command()
def version(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Print the installed version."""
    payload = {"name": "quelle", "version": __version__}
    if json_output:
        emit_json(payload)
    else:
        typer.echo(f"{payload['name']} {payload['version']}")


@app.command()
def init() -> None:
    """Create config/data/cache directories and seed a default .env if missing."""
    init_command()


@app.command()
def fetch(
    query: str = typer.Argument(..., help="DOI, arXiv id, ISBN, or free-text title."),
    author: str = typer.Option(
        None,
        "--author",
        help="Author hint for free-text title queries (used to disambiguate).",
    ),
    result_type: str = typer.Option(
        "all",
        "--type",
        help=(
            "Bias resolution toward book / article sources for free-text queries. "
            "Ignored when the query is an explicit DOI / ISBN / arXiv id."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Bypass the local cache (always hit the network)."
    ),
    download_pdf: bool = typer.Option(
        False,
        "--download-pdf",
        "-d",
        help="Also download the OA PDF when available.",
    ),
) -> None:
    """Resolve a publication from open sources and print its metadata."""
    from dataclasses import replace

    if result_type not in {"book", "article", "all"}:
        _report(UserError(f"--type must be one of: book, article, all (got {result_type!r})"))
        raise typer.Exit(1)

    effective_query = query
    effective_author = author
    if effective_author is None and not _looks_like_explicit_id(query):
        effective_query, parsed_author = _split_author_from_query(query)
        if parsed_author is not None:
            effective_author = parsed_author

    type_hint = result_type if result_type != "all" else None

    settings = _load()
    mode = OutputMode.detect(json_output)
    try:
        with build_client(settings) as client:
            if no_cache:
                publication = resolve_with_enrichment(
                    client,
                    settings,
                    effective_query,
                    type_hint=type_hint,
                    author=effective_author,
                )
                cache_handle = None
            else:
                cache_handle = Cache.open(settings.paths.cache_db)
                publication = resolve_with_enrichment(
                    client,
                    settings,
                    effective_query,
                    cache=cache_handle,
                    type_hint=type_hint,
                    author=effective_author,
                )
            if download_pdf:
                from quelle.services.pdf_resolver import resolve_and_download

                outcome = resolve_and_download(
                    client, settings, publication, settings.paths.pdf_dir
                )
                if outcome.local_path is not None:
                    publication = replace(publication, local_pdf_path=str(outcome.local_path))
                    if cache_handle is not None:
                        cache_handle.upsert(publication)
    except PublicationsError as exc:
        _report(exc)
        raise typer.Exit(_exit_code(exc)) from exc
    finally:
        if not no_cache and "cache_handle" in locals() and cache_handle is not None:
            cache_handle.close()
    render_publication(_publication_to_dict(publication), mode=mode)


@app.command()
def search(
    query: str = typer.Argument(
        ...,
        help="Free-text title query (or any text the sources accept).",
    ),
    author: str = typer.Option(
        None,
        "--author",
        help="Author hint, used as a native filter where supported.",
    ),
    result_type: str = typer.Option(
        "all",
        "--type",
        help="Restrict to book / article sources, or query both. One of: book, article, all.",
    ),
    limit: int = typer.Option(3, "--limit", help="Number of merged hits to return."),
    source: list[str] = typer.Option(
        None, "--source", help="Repeatable. Restrict to named sources."
    ),
    no_source: list[str] = typer.Option(
        None, "--no-source", help="Repeatable. Exclude named sources."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """List candidate publications across multiple open sources.

    Hits from each source are merged via Reciprocal Rank Fusion and
    deduplicated by DOI / ISBN-13 / arXiv id. Each line ends with an
    `id:` value that can be passed to `quelle fetch`.
    """
    if result_type not in {"book", "article", "all"}:
        _report(UserError(f"--type must be one of: book, article, all (got {result_type!r})"))
        raise typer.Exit(1)

    effective_query = query
    effective_author = author
    if effective_author is None:
        effective_query, parsed_author = _split_author_from_query(query)
        if parsed_author is not None:
            effective_author = parsed_author

    settings = _load()
    mode = OutputMode.detect(json_output)
    try:
        with build_client(settings) as client:
            hits = search_service.search(
                client,
                settings,
                effective_query,
                author=effective_author,
                type=result_type,  # type: ignore[arg-type]
                sources=source or None,
                no_sources=no_source or None,
                limit=limit,
            )
    except PublicationsError as exc:
        _report(exc)
        raise typer.Exit(_exit_code(exc)) from exc

    payload = {
        "query": effective_query,
        "author": effective_author,
        "type": result_type,
        "limit": limit,
        "hits": [_hit_to_dict(rank, hit) for rank, hit in enumerate(hits, start=1)],
    }
    render_search(payload, mode=mode)


def _looks_like_explicit_id(query: str) -> bool:
    """Cheap check: does the query look like a DOI, ISBN, or arXiv id?

    Used to suppress the comma-split heuristic on `quelle fetch` for
    explicit-id queries, since those occasionally contain commas
    (DOIs especially) and have no need for an author hint.
    """
    import re

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


def _split_author_from_query(query: str) -> tuple[str, str | None]:
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


@cache_app.command("stats")
def cache_stats(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Show the cache size, schema version, and last upsert time."""
    settings = _load()
    mode = OutputMode.detect(json_output)
    with Cache.open(settings.paths.cache_db) as cache:
        payload = cache.stats()
    payload["cache_db"] = str(settings.paths.cache_db)
    render_config(payload, mode=mode)


@cache_app.command("list")
def cache_list(
    limit: int = typer.Option(50, "--limit", help="Max rows to list."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """List the most recently cached publications."""
    settings = _load()
    mode = OutputMode.detect(json_output)
    with Cache.open(settings.paths.cache_db) as cache:
        entries = cache.list_entries(limit=limit)
    render_cache_list({"entries": entries}, mode=mode)


@cache_app.command("clear")
def cache_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm destructive wipe."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Delete every row from the local cache (irreversible)."""
    if not yes:
        raise typer.Exit(_handle_user(UserError("pass --yes to confirm cache wipe")))
    settings = _load()
    mode = OutputMode.detect(json_output)
    with Cache.open(settings.paths.cache_db) as cache:
        removed = cache.clear()
    render_config({"cleared_rows": removed}, mode=mode)


@cache_app.command("show")
def cache_show(
    query: str = typer.Argument(..., help="DOI, arXiv id, or title to look up."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Look up a publication in the cache without hitting the network."""
    from quelle.services.resolver import _lookup_in_cache

    settings = _load()
    mode = OutputMode.detect(json_output)
    with Cache.open(settings.paths.cache_db) as cache:
        hit = _lookup_in_cache(cache, query)
    if hit is None:
        _report(NotFoundError(f"no cached entry for: {query!r}"))
        raise typer.Exit(1)
    render_publication(_publication_to_dict(hit), mode=mode)


def _handle_user(exc: UserError) -> int:
    _report(exc)
    return 1


def _publication_to_dict(publication: Publication) -> dict:
    """Flatten a Publication dataclass into a JSON-serialisable dict."""
    data = asdict(publication)
    data["citation_key"] = publication.citation_key()
    return data


def _hit_to_dict(rank: int, hit: MergedHit) -> dict:
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


if __name__ == "__main__":
    app()
