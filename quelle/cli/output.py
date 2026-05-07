"""Output helpers — JSON vs rich TTY rendering.

Every CLI command passes a plain dict through one of these helpers.
In `--json` mode we emit JSON to stdout. In TTY mode we render with
rich. In plain non-TTY mode we emit a compact plain-text rendering.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_console = Console()


@dataclass
class OutputMode:
    """Whether the caller wants JSON, a rich TTY render, or plain text."""

    json: bool
    tty: bool

    @classmethod
    def detect(cls, json_flag: bool) -> OutputMode:
        return cls(json=json_flag, tty=sys.stdout.isatty() and not json_flag)


def emit_json(payload: Any) -> None:
    """Write a JSON payload to stdout with a trailing newline."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    sys.stdout.write("\n")


def render_publication(payload: dict[str, Any], *, mode: OutputMode) -> None:
    """Pretty-print a single publication, or emit its JSON."""
    if mode.json:
        emit_json(payload)
        return
    title = payload.get("title") or "(no title)"
    year = payload.get("year")
    venue = payload.get("venue")
    doi = payload.get("doi")
    pdf_url = payload.get("pdf_url")
    citation_key = payload.get("citation_key")
    authors = payload.get("authors") or []
    kind = payload.get("kind")
    is_book = kind in {"book", "book-chapter"}

    authors_line = ", ".join(author.get("name", "") for author in authors[:5])
    if len(authors) > 5:
        authors_line += f" (+{len(authors) - 5} more)"

    header_lines: list[str] = [f"[bold]{title}[/bold]"]
    if authors_line:
        header_lines.append(authors_line)
    meta_bits: list[str] = []
    if year:
        meta_bits.append(str(year))
    if is_book:
        publisher = payload.get("publisher")
        if publisher:
            meta_bits.append(publisher)
        edition = payload.get("edition")
        if edition:
            meta_bits.append(edition)
        isbn = payload.get("isbn_13") or payload.get("isbn_10")
        if isbn:
            meta_bits.append(f"isbn:{isbn}")
    else:
        if venue:
            meta_bits.append(venue)
        if doi:
            meta_bits.append(f"doi:{doi}")
    if citation_key:
        meta_bits.append(f"cite:{citation_key}")
    if meta_bits:
        header_lines.append("[dim]" + " · ".join(meta_bits) + "[/dim]")
    if pdf_url:
        header_lines.append(f"[green]PDF[/green]: {pdf_url}")
    elif not is_book:
        header_lines.append("[yellow]no PDF found[/yellow]")

    _console.print(Panel("\n".join(header_lines), expand=False))

    abstract = payload.get("abstract")
    if abstract:
        _console.print("[bold]Abstract[/bold]" if not is_book else "[bold]Description[/bold]")
        _console.print(abstract)


def render_config(payload: dict[str, Any], *, mode: OutputMode) -> None:
    """Render a flat key/value config payload."""
    if mode.json:
        emit_json(payload)
        return
    table = Table(
        title="quelle",
        show_header=False,
        box=None,
        padding=(0, 1),
    )
    table.add_column(style="bold")
    table.add_column()
    for key, value in payload.items():
        table.add_row(key, str(value) if value is not None else "[dim]—[/dim]")
    _console.print(table)


def render_search(payload: dict[str, Any], *, mode: OutputMode) -> None:
    """Render a numbered list of search hits, or emit JSON."""
    if mode.json:
        emit_json(payload)
        return
    hits = payload.get("hits") or []
    if not hits:
        _console.print("[dim]no matches[/dim]")
        return
    for entry in hits:
        rank = entry.get("rank")
        title = entry.get("title") or "(no title)"
        authors = entry.get("authors") or []
        authors_line = ", ".join(a.get("name", "") for a in authors[:3])
        if len(authors) > 3:
            authors_line += " et al."
        year = entry.get("year")
        hit_type = entry.get("type") or "unknown"
        sources = entry.get("sources") or []
        id_str = entry.get("id") or "[dim]—[/dim]"
        resolvable = entry.get("id_resolvable", True)
        suffix = "" if resolvable else "[yellow]*[/yellow]"

        head = f"[bold]{rank}.[/bold] {title}"
        meta_bits = []
        if authors_line:
            meta_bits.append(authors_line)
        if year:
            meta_bits.append(str(year))
        meta_bits.append(f"[dim]\\[{hit_type}][/dim]")
        _console.print(f"{head} — {' · '.join(meta_bits)}")
        sources_line = ", ".join(sources)
        _console.print(f"   id: [cyan]{id_str}[/cyan]{suffix}   sources: [dim]{sources_line}[/dim]")


def render_cache_list(payload: dict[str, Any], *, mode: OutputMode) -> None:
    """Render a list of cache entries."""
    if mode.json:
        emit_json(payload)
        return
    entries = payload.get("entries") or []
    if not entries:
        _console.print("[dim]cache is empty[/dim]")
        return
    table = Table(title=f"{len(entries)} cached publication(s)")
    table.add_column("Citekey", style="bold")
    table.add_column("DOI", style="cyan")
    table.add_column("Title", overflow="fold")
    table.add_column("Cached at", style="dim")
    for entry in entries:
        table.add_row(
            entry.get("citation_key", ""),
            entry.get("doi") or "",
            (entry.get("title_key") or "")[:80],
            (entry.get("cached_at") or "")[:19],
        )
    _console.print(table)
