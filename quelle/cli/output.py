"""Output helpers — JSON vs rich rendering.

Every CLI command passes a plain dict through one of these helpers.
In `--json` mode we emit JSON to stdout; otherwise we render with rich
(which itself degrades markup to plain text on a non-TTY stream).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

_console = Console()


@dataclass(frozen=True)
class OutputMode:
    """Whether the caller wants JSON or the rich rendering."""

    json: bool

    @classmethod
    def detect(cls, json_flag: bool) -> OutputMode:
        return cls(json=json_flag)

    @classmethod
    def from_ctx(cls, ctx: Any) -> OutputMode:
        """Resolve the output mode from a Typer context's stashed `--json` flag."""
        json_flag = bool(getattr(ctx, "obj", None) and ctx.obj.get("json"))
        return cls.detect(json_flag)


def emit_json(payload: Any) -> None:
    """Write a JSON payload to stdout with a trailing newline."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    sys.stdout.write("\n")


def _format_bytes(value: Any) -> str:
    """Render a byte count as a short human label (KB / MB / GB)."""
    if not isinstance(value, int) or value < 0:
        return "?"
    if value < 1024:
        return f"{value} B"
    units = ("KB", "MB", "GB", "TB")
    size = float(value) / 1024
    for unit in units[:-1]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} {units[-1]}"


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
    """Render a numbered list of search hits, or emit JSON.

    Three lines per hit:
      1. rank + title (bold)
      2. authors · year · type   (dim)
      3. id   ·   sources        (id in green when resolvable, yellow otherwise)
    A blank line separates hits.
    """
    if mode.json:
        emit_json(payload)
        return
    hits = payload.get("hits") or []
    if not hits:
        _console.print("[dim]no matches[/dim]")
        return
    for index, entry in enumerate(hits):
        if index > 0:
            _console.print()
        rank = entry.get("rank")
        title = escape(entry.get("title") or "(no title)")

        authors = entry.get("authors") or []
        author_names = [a.get("name", "") for a in authors[:3] if a.get("name")]
        authors_line = ", ".join(escape(name) for name in author_names)
        if len(authors) > 3:
            authors_line += f" (+{len(authors) - 3})"

        year = entry.get("year")
        hit_type = entry.get("type") or "unknown"
        id_str = entry.get("id")
        resolvable = entry.get("id_resolvable", True)
        sources = entry.get("sources") or []

        _console.print(f"[bold cyan]{rank:>2}.[/bold cyan]  [bold]{title}[/bold]")

        byline_bits: list[str] = []
        if authors_line:
            byline_bits.append(authors_line)
        if year:
            byline_bits.append(str(year))
        byline_bits.append(hit_type)
        _console.print(f"     [dim]{' · '.join(byline_bits)}[/dim]")

        if id_str:
            colour = "green" if resolvable else "yellow"
            id_segment = f"[{colour}]{escape(id_str)}[/{colour}]"
            if not resolvable:
                id_segment += " [dim](not accepted by quelle fetch)[/dim]"
        else:
            id_segment = "[dim]no identifier[/dim]"
        if sources:
            id_segment += f"   [dim]· {', '.join(escape(s) for s in sources)}[/dim]"
        _console.print(f"     {id_segment}")


def render_cache_list(payload: dict[str, Any], *, mode: OutputMode) -> None:
    """Render a list of cache entries with a header summary.

    The header carries the total row count, schema version, last
    upsert timestamp, oldest entry, and on-disk size in human form.
    """
    if mode.json:
        emit_json(payload)
        return
    entries = payload.get("entries") or []
    total = payload.get("total", 0)
    newest = (payload.get("newest_cached_at") or "")[:19] or "(empty)"
    oldest = (payload.get("oldest_cached_at") or "")[:19] or "(empty)"
    schema = payload.get("schema_version", "?")
    size_label = _format_bytes(payload.get("size_bytes"))
    header_parts = [
        f"last upsert {newest}",
        f"oldest {oldest}",
        f"size {size_label}",
        f"schema v{schema}",
    ]
    _console.print(
        f"[bold]cache:[/bold] {total} entr{'y' if total == 1 else 'ies'}  "
        f"[dim]· {' · '.join(header_parts)}[/dim]"
    )
    if not entries:
        return
    table = Table(box=None, padding=(0, 1))
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
