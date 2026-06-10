"""Typer CLI entrypoint for the `quelle` command.

Each subcommand is a thin wrapper: parse flags, load Settings, open an
httpx client, call the resolver, render the result via
`quelle.cli.output`.

Exit codes (mapped from exception types in `quelle.repositories.errors`):
    0  success
    1  user error / not found
    2  network error / rate limit
    3  cache error
    4  config error
    64 CLI usage error (bad flags / arguments — click's domain)
"""

from __future__ import annotations

from dataclasses import replace

import typer

from quelle import __version__
from quelle.cli._helpers import (
    EX_USAGE,
    exit_code_for,
    hit_to_dict,
    load_settings_or_exit,
    load_taken_set,
    looks_like_explicit_id,
    publication_to_csl,
    publication_to_dict,
    report_error,
    resolve_type_hint,
    split_author_from_query,
)
from quelle.cli.config import config_app
from quelle.cli.output import (
    OutputMode,
    emit_json,
    render_cache_list,
    render_config,
    render_publication,
    render_search,
)
from quelle.cli.skill import skill_app
from quelle.repositories.cache import Cache
from quelle.repositories.errors import (
    NotFoundError,
    PublicationsError,
    UserError,
)
from quelle.repositories.http_client import build_client
from quelle.services import search as search_service
from quelle.services.citekey import base_key, mint
from quelle.services.resolver import resolve_any, resolve_with_enrichment
from quelle.services.search import SearchType
from quelle.settings import Settings

# Click defaults usage errors (bad flags / missing arguments) to exit 2,
# which collides with our documented "network error" code. Repoint it at
# EX_USAGE (64) once, before any command runs. typer >= 0.25 vendors its
# own copy of click; older typer uses the standalone package.
try:
    from typer._click.exceptions import UsageError as _ClickUsageError
except ImportError:  # pragma: no cover — typer < 0.25
    from click.exceptions import UsageError as _ClickUsageError  # type: ignore[no-redef]

_ClickUsageError.exit_code = EX_USAGE

app = typer.Typer(
    help="Fetch publication metadata and PDFs from open academic APIs.",
    no_args_is_help=True,
    add_completion=False,
)

cache_app = typer.Typer(help="Inspect the local SQLite cache.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")
app.add_typer(config_app, name="config")
app.add_typer(skill_app, name="skill")

# Documented hard ceiling on `--limit`. Each upstream caps `per-page` lower
# than this (Google Books at 40, Semantic Scholar at 100, OpenAlex at 200);
# `services/search.py` clips per-source pulls accordingly. Returning more
# than 50 merged hits to a human in one go is rarely useful anyway.
MAX_LIMIT = 50

# Map the (--book, --article) flag pair to the search service's typed
# Literal so we don't smuggle a plain `str` through `# type: ignore`.
_TYPE_TO_LITERAL: dict[str | None, SearchType] = {
    None: "all",
    "book": "book",
    "article": "article",
}


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


def _load() -> Settings:
    return load_settings_or_exit()


def _type_hint_or_exit(book: bool, article: bool) -> str | None:
    """Wrap `resolve_type_hint` with the Typer-side error reporting."""
    try:
        return resolve_type_hint(book, article)
    except UserError as exc:
        report_error(exc)
        raise typer.Exit(1) from exc


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
    """Resolve a publication from open sources and print its metadata.

    `--book` / `--article` only steer free-text resolution. When the
    query is an explicit DOI / ISBN / arXiv id the flag is ignored —
    we now reject the combination instead of silently throwing it
    away, so the user knows to drop the flag (or pass a free-text
    title query).
    """
    type_hint = _type_hint_or_exit(book, article)

    explicit = looks_like_explicit_id(query)
    if explicit and type_hint is not None:
        report_error(
            UserError(
                f"--{type_hint} is for free-text queries; the explicit "
                f"identifier {query!r} resolves directly. Drop the flag, "
                "or pass the title as free text."
            )
        )
        raise typer.Exit(1)

    effective_query = query
    effective_author: str | None = None
    if not explicit:
        effective_query, effective_author = split_author_from_query(query)

    settings = _load()
    try:
        with build_client(settings) as client:
            cache_handle: Cache | None = None
            if no_cache:
                publication = _fetch_with_cache(
                    client,
                    settings,
                    effective_query,
                    cache_handle=None,
                    type_hint=type_hint,
                    author=effective_author,
                    download_pdf=download_pdf,
                )
            else:
                with Cache.open(settings.paths.cache_db) as cache_handle:
                    publication = _fetch_with_cache(
                        client,
                        settings,
                        effective_query,
                        cache_handle=cache_handle,
                        type_hint=type_hint,
                        author=effective_author,
                        download_pdf=download_pdf,
                    )
    except PublicationsError as exc:
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc
    render_publication(publication_to_dict(publication), mode=OutputMode.from_ctx(ctx))


def _fetch_with_cache(
    client,
    settings: Settings,
    query: str,
    *,
    cache_handle: Cache | None,
    type_hint: str | None,
    author: str | None,
    download_pdf: bool,
):
    """Resolve, optionally download the PDF, persist back to the cache."""
    publication = resolve_with_enrichment(
        client,
        settings,
        query,
        cache=cache_handle,
        type_hint=type_hint,
        author=author,
    )
    if download_pdf:
        from quelle.services.pdf_resolver import resolve_and_download

        outcome = resolve_and_download(client, settings, publication, settings.paths.pdf_dir)
        if outcome.local_path is not None:
            publication = replace(publication, local_pdf_path=str(outcome.local_path))
            if cache_handle is not None:
                cache_handle.upsert(publication)
    return publication


@app.command()
def resolve(
    ctx: typer.Context,
    input: str = typer.Argument(
        ...,
        help="Anything: a local .pdf path, an http(s) URL, a DOI / ISBN / "
        'arXiv id, or "Title[, Author]".',
    ),
    taken: str = typer.Option(
        None, "--taken", help="Comma-separated CiteKeys already taken in the vault."
    ),
    taken_file: str = typer.Option(
        None,
        "--taken-file",
        help="File of taken CiteKeys (newline list or `knoten citekeys --json`); `-` reads stdin.",
    ),
    csl: bool = typer.Option(
        False, "--csl", help="Emit a CSL-JSON item instead of the Source dict."
    ),
    download_pdf: bool = typer.Option(
        False, "--download-pdf", "-d", help="Also download the OA PDF when available."
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Bypass the local cache (always hit the network)."
    ),
    book: bool = typer.Option(False, "--book", help="Bias toward book sources (free-text only)."),
    article: bool = typer.Option(
        False, "--article", help="Bias toward article sources (free-text only)."
    ),
) -> None:
    """Resolve ANY source to a Publication and mint its vault-ready CiteKey.

    Routes by input shape — local PDF, web/media URL, DOI/ISBN/arXiv id,
    or free text — and always returns a normalised Source: the Publication
    dict plus a top-level `x_vcoeur` block whose `citekey` is minted against
    the injected taken-set (`--taken` / `--taken-file`). `--csl` exports a
    CSL-JSON item instead. `--book` / `--article` only steer free text.
    """
    type_hint = _type_hint_or_exit(book, article)
    try:
        taken_keys = load_taken_set(taken, taken_file)
    except (OSError, ValueError) as exc:
        report_error(UserError(f"could not read taken-set: {exc}"))
        raise typer.Exit(1) from exc

    settings = _load()
    try:
        with build_client(settings) as client:
            if no_cache:
                publication = _resolve_any_with_pdf(
                    client,
                    settings,
                    input,
                    cache_handle=None,
                    type_hint=type_hint,
                    download_pdf=download_pdf,
                )
            else:
                with Cache.open(settings.paths.cache_db) as cache_handle:
                    publication = _resolve_any_with_pdf(
                        client,
                        settings,
                        input,
                        cache_handle=cache_handle,
                        type_hint=type_hint,
                        download_pdf=download_pdf,
                    )
    except PublicationsError as exc:
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc

    minted = mint(base_key(publication), taken_keys)
    if csl:
        emit_json(publication_to_csl(publication, citekey=minted))
        return
    render_publication(
        publication_to_dict(publication, citekey=minted),
        mode=OutputMode.from_ctx(ctx),
    )


def _resolve_any_with_pdf(
    client,
    settings: Settings,
    raw_input: str,
    *,
    cache_handle: Cache | None,
    type_hint: str | None,
    download_pdf: bool,
):
    """Route the input through `resolve_any`, then optionally download a PDF."""
    publication = resolve_any(
        client,
        settings,
        raw_input,
        cache=cache_handle,
        type_hint=type_hint,
    )
    if download_pdf and publication.local_pdf_path is None:
        from quelle.services.pdf_resolver import resolve_and_download

        outcome = resolve_and_download(client, settings, publication, settings.paths.pdf_dir)
        if outcome.local_path is not None:
            publication = replace(publication, local_pdf_path=str(outcome.local_path))
            if cache_handle is not None:
                cache_handle.upsert(publication)
    return publication


@app.command("schema")
def cmd_schema(ctx: typer.Context) -> None:
    """Dump the machine-readable CLI contract — commands, flags, Source
    fields, the x_vcoeur block, the CiteKey rules, the kind map, and exit
    codes. No network, no cache access.

    Lets a client (or an LLM) self-orient from one call: every command and
    its flags are introspected from the live app, and the static tables are
    read from the modules that own them, so the output never drifts.
    """
    from quelle.cli.introspect import command_listing
    from quelle.services.schema import build_schema

    payload = build_schema(commands=command_listing(app))
    mode = OutputMode.from_ctx(ctx)
    if mode.json:
        emit_json(payload)
        return
    from rich.console import Console

    console = Console()
    console.print(f"[bold]quelle {payload['version']}[/bold] — {len(payload['commands'])} commands")
    console.print("[bold]kinds:[/bold] " + ", ".join(payload["kinds"]))
    console.print(
        "[bold]kind map:[/bold] " + ", ".join(f"{k}→{v}" for k, v in payload["kind_map"].items())
    )
    console.print(
        "[bold]exit codes:[/bold] "
        + ", ".join(f"{e['code']}={e['meaning']}" for e in payload["exit_codes"])
    )
    console.print("[dim]pass --json (before the subcommand) for the full contract[/dim]")


@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(
        ...,
        help='Free-text title, or "Title, Author".',
    ),
    book: bool = typer.Option(False, "--book", help="Restrict to book sources."),
    article: bool = typer.Option(False, "--article", help="Restrict to article sources."),
    limit: int = typer.Option(
        3,
        "--limit",
        min=1,
        max=MAX_LIMIT,
        help=f"Number of merged hits to return (1-{MAX_LIMIT}).",
    ),
    source: list[str] = typer.Option(
        None, "--source", help="Repeatable. Restrict to named sources."
    ),
) -> None:
    """List candidate publications across multiple open sources.

    Hits from each source are merged via Reciprocal Rank Fusion and
    deduplicated by DOI / ISBN / arXiv id. Each line ends with an
    `id:` value that can be passed to `quelle fetch`.
    """
    type_hint = _type_hint_or_exit(book, article)
    result_type = _TYPE_TO_LITERAL[type_hint]

    effective_query, effective_author = split_author_from_query(query)

    settings = _load()
    try:
        with build_client(settings) as client:
            hits = search_service.search(
                client,
                settings,
                effective_query,
                author=effective_author,
                type=result_type,
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
    render_search(payload, mode=OutputMode.from_ctx(ctx))


@cache_app.command("list")
def cache_list(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit", min=1, help="Max rows to list."),
) -> None:
    """List the most recently cached publications, with a header summary.

    The header line carries the total row count, schema version, and
    last-upsert timestamp — the data the previous `cache stats` command
    used to print on its own.
    """
    settings = _load()
    try:
        with Cache.open(settings.paths.cache_db) as cache:
            stats = cache.stats()
            entries = cache.list_entries(limit=limit)
    except PublicationsError as exc:
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc
    payload = {
        **stats,
        "cache_db": str(settings.paths.cache_db),
        "entries": entries,
    }
    render_cache_list(payload, mode=OutputMode.from_ctx(ctx))


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
    try:
        with Cache.open(settings.paths.cache_db) as cache:
            removed = cache.clear()
    except PublicationsError as exc:
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc
    render_config({"cleared_rows": removed}, mode=OutputMode.from_ctx(ctx))


@cache_app.command("show")
def cache_show(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="DOI, arXiv id, ISBN, or title to look up."),
) -> None:
    """Look up a publication in the cache without hitting the network."""
    settings = _load()
    try:
        with Cache.open(settings.paths.cache_db) as cache:
            hit = cache.lookup(query)
    except PublicationsError as exc:
        report_error(exc)
        raise typer.Exit(exit_code_for(exc)) from exc
    if hit is None:
        report_error(NotFoundError(f"no cached entry for: {query!r}"))
        raise typer.Exit(1)
    render_publication(publication_to_dict(hit), mode=OutputMode.from_ctx(ctx))


if __name__ == "__main__":
    app()
