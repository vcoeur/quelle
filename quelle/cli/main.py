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

from dataclasses import replace

import typer

from quelle import __version__
from quelle.cli._helpers import (
    exit_code_for,
    hit_to_dict,
    looks_like_explicit_id,
    publication_to_dict,
    report_error,
    split_author_from_query,
)
from quelle.cli.config import config_app
from quelle.cli.output import (
    OutputMode,
    render_cache_list,
    render_config,
    render_publication,
    render_search,
)
from quelle.repositories.cache import Cache
from quelle.repositories.errors import (
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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of text. Place before the subcommand.",
    ),
) -> None:
    """Root callback — handles the global `--version` and `--json` flags.

    `invoke_without_command=True` lets `quelle --version` short-circuit
    without requiring a subcommand; a bare `quelle` with no subcommand
    still falls through to the help view via `no_args_is_help=True`.
    `--json` is stashed on `ctx.obj` so every subcommand reads from one
    place instead of declaring its own flag.
    """
    if version:
        typer.echo(f"quelle {__version__}")
        raise typer.Exit(0)
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output


def _mode(ctx: typer.Context) -> OutputMode:
    """Resolve the output mode from the root `--json` flag."""
    json_flag = bool(ctx.obj and ctx.obj.get("json"))
    return OutputMode.detect(json_flag)


def _load() -> Settings:
    return load_settings()


def _resolve_type_hint(book: bool, article: bool) -> str | None:
    """Translate the mutually-exclusive `--book` / `--article` flags into a hint.

    Both absent → `None` (query every source). Both present is a user error;
    fail fast before touching settings or the network.
    """
    if book and article:
        report_error(UserError("--book and --article are mutually exclusive"))
        raise typer.Exit(1)
    if book:
        return "book"
    if article:
        return "article"
    return None


@app.command()
def fetch(
    ctx: typer.Context,
    query: str = typer.Argument(
        ...,
        help='DOI, arXiv id, ISBN, or "Title[, Author]".',
    ),
    book: bool = typer.Option(False, "--book", help="Bias toward book sources (free-text only)."),
    article: bool = typer.Option(
        False, "--article", help="Bias toward article sources (free-text only)."
    ),
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
    type_hint = _resolve_type_hint(book, article)

    effective_query = query
    effective_author: str | None = None
    if not looks_like_explicit_id(query):
        effective_query, effective_author = split_author_from_query(query)

    settings = _load()
    cache_handle: Cache | None = None
    try:
        with build_client(settings) as client:
            cache_handle = None if no_cache else Cache.open(settings.paths.cache_db)
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
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc
    finally:
        if cache_handle is not None:
            cache_handle.close()
    render_publication(publication_to_dict(publication), mode=_mode(ctx))


@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(
        ...,
        help='Free-text title, or "Title, Author".',
    ),
    book: bool = typer.Option(False, "--book", help="Restrict to book sources."),
    article: bool = typer.Option(False, "--article", help="Restrict to article sources."),
    limit: int = typer.Option(3, "--limit", help="Number of merged hits to return."),
    source: list[str] = typer.Option(
        None, "--source", help="Repeatable. Restrict to named sources."
    ),
) -> None:
    """List candidate publications across multiple open sources.

    Hits from each source are merged via Reciprocal Rank Fusion and
    deduplicated by DOI / ISBN / arXiv id. Each line ends with an
    `id:` value that can be passed to `quelle fetch`.
    """
    type_hint = _resolve_type_hint(book, article)
    result_type = type_hint or "all"

    effective_query, effective_author = split_author_from_query(query)

    settings = _load()
    try:
        with build_client(settings) as client:
            hits = search_service.search(
                client,
                settings,
                effective_query,
                author=effective_author,
                type=result_type,  # type: ignore[arg-type]
                sources=source or None,
                limit=limit,
            )
    except PublicationsError as exc:
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc

    payload = {
        "query": effective_query,
        "author": effective_author,
        "type": result_type,
        "limit": limit,
        "hits": [hit_to_dict(rank, hit) for rank, hit in enumerate(hits, start=1)],
    }
    render_search(payload, mode=_mode(ctx))


@cache_app.command("list")
def cache_list(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit", help="Max rows to list."),
) -> None:
    """List the most recently cached publications, with a header summary.

    The header line carries the total row count, schema version, and
    last-upsert timestamp — the data the previous `cache stats` command
    used to print on its own.
    """
    settings = _load()
    with Cache.open(settings.paths.cache_db) as cache:
        stats = cache.stats()
        entries = cache.list_entries(limit=limit)
    payload = {
        **stats,
        "cache_db": str(settings.paths.cache_db),
        "entries": entries,
    }
    render_cache_list(payload, mode=_mode(ctx))


@cache_app.command("clear")
def cache_clear(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm destructive wipe."),
) -> None:
    """Delete every row from the local cache (irreversible)."""
    if not yes:
        report_error(UserError("pass --yes to confirm cache wipe"))
        raise typer.Exit(1)
    settings = _load()
    with Cache.open(settings.paths.cache_db) as cache:
        removed = cache.clear()
    render_config({"cleared_rows": removed}, mode=_mode(ctx))


@cache_app.command("show")
def cache_show(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="DOI, arXiv id, ISBN, or title to look up."),
) -> None:
    """Look up a publication in the cache without hitting the network."""
    from quelle.services.resolver import lookup_in_cache

    settings = _load()
    with Cache.open(settings.paths.cache_db) as cache:
        hit = lookup_in_cache(cache, query)
    if hit is None:
        report_error(NotFoundError(f"no cached entry for: {query!r}"))
        raise typer.Exit(1)
    render_publication(publication_to_dict(hit), mode=_mode(ctx))


if __name__ == "__main__":
    app()
