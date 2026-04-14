"""Typer CLI entrypoint for the `publications` command.

Each subcommand is a thin wrapper: parse flags, load Settings, open an
httpx client, call the resolver, render the result via
`app.cli.output`.

Exit codes (mapped from exception types in `app.repositories.errors`):
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

from app.cli.output import (
    OutputMode,
    emit_json,
    render_cache_list,
    render_config,
    render_publication,
)
from app.models.publication import Publication
from app.repositories.cache import Cache
from app.repositories.errors import (
    CacheError,
    ConfigError,
    NetworkError,
    NotFoundError,
    PublicationsError,
    UserError,
)
from app.repositories.http_client import build_client
from app.services.resolver import resolve_with_enrichment
from app.settings import Settings, ensure_dirs, load_settings

app = typer.Typer(
    help="Fetch publication metadata and PDFs from open academic APIs.",
    no_args_is_help=True,
    add_completion=False,
)

cache_app = typer.Typer(help="Inspect the local SQLite cache.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")


def _load() -> Settings:
    settings = load_settings()
    ensure_dirs(settings)
    return settings


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
        "Local cache failure — the SQLite file may be corrupt. "
        "Try `publications cache clear --yes`."
    ),
    UserError: "Invalid input — `publications fetch --help` for usage.",
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
    payload = {"name": "publication-manager", "version": "0.1.0"}
    if json_output:
        emit_json(payload)
    else:
        typer.echo(f"{payload['name']} {payload['version']}")


@app.command("config")
def config_command(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Show the effective configuration (env + .env layers)."""
    settings = _load()
    mode = OutputMode.detect(json_output)
    payload = {
        "home": str(settings.home),
        "state_dir": str(settings.state_dir),
        "cache_db": str(settings.cache_db),
        "pdf_dir": str(settings.pdf_dir),
        "openalex_api_key": settings.openalex_key_redacted or "(unset)",
        "unpaywall_email": settings.unpaywall_email or "(unset)",
        "contact_email": settings.contact_email or "(unset)",
        "user_agent": settings.user_agent,
        "http_timeout": settings.http_timeout,
    }
    render_config(payload, mode=mode)


@app.command()
def fetch(
    query: str = typer.Argument(..., help="DOI, arXiv id, or free-text title."),
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

    settings = _load()
    mode = OutputMode.detect(json_output)
    try:
        with build_client(settings) as client:
            if no_cache:
                publication = resolve_with_enrichment(client, settings, query)
                cache_handle = None
            else:
                cache_handle = Cache.open(settings.cache_db)
                publication = resolve_with_enrichment(client, settings, query, cache=cache_handle)
            if download_pdf:
                from app.services.pdf_resolver import resolve_and_download

                outcome = resolve_and_download(client, settings, publication, settings.pdf_dir)
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


@cache_app.command("stats")
def cache_stats(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Show the cache size, schema version, and last upsert time."""
    settings = _load()
    mode = OutputMode.detect(json_output)
    with Cache.open(settings.cache_db) as cache:
        payload = cache.stats()
    payload["cache_db"] = str(settings.cache_db)
    render_config(payload, mode=mode)


@cache_app.command("list")
def cache_list(
    limit: int = typer.Option(50, "--limit", help="Max rows to list."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """List the most recently cached publications."""
    settings = _load()
    mode = OutputMode.detect(json_output)
    with Cache.open(settings.cache_db) as cache:
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
    with Cache.open(settings.cache_db) as cache:
        removed = cache.clear()
    render_config({"cleared_rows": removed}, mode=mode)


@cache_app.command("show")
def cache_show(
    query: str = typer.Argument(..., help="DOI, arXiv id, or title to look up."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Look up a publication in the cache without hitting the network."""
    from app.services.resolver import _lookup_in_cache

    settings = _load()
    mode = OutputMode.detect(json_output)
    with Cache.open(settings.cache_db) as cache:
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


if __name__ == "__main__":
    app()
