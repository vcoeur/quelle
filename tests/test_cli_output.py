"""Rendering tests for `quelle/cli/output.py` — the rich (non-JSON) paths.

The JSON branches are exercised end-to-end by the CLI tests; these call
the render functions directly and assert on key content strings, not on
exact formatting (box characters, colour, wrapping are rich's business).
"""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from quelle.cli import output as output_module
from quelle.cli.output import (
    OutputMode,
    _format_bytes,
    render_cache_list,
    render_publication,
    render_search,
)

RICH = OutputMode(json=False)


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render at a generous width so the content-string asserts below can
    rely on contiguous substrings instead of fighting rich's wrapping."""
    monkeypatch.setattr(output_module, "_console", Console(width=200))


# --- _format_bytes -----------------------------------------------------------


def test_format_bytes_rejects_non_int_and_negative() -> None:
    assert _format_bytes(None) == "?"
    assert _format_bytes("4096") == "?"
    assert _format_bytes(-1) == "?"


def test_format_bytes_scales_units() -> None:
    assert _format_bytes(512) == "512 B"
    assert _format_bytes(2048) == "2.0 KB"
    assert _format_bytes(5 * 1024**2) == "5.0 MB"
    assert _format_bytes(3 * 1024**3) == "3.0 GB"
    assert _format_bytes(2 * 1024**4) == "2.0 TB"


def test_format_bytes_caps_at_terabytes() -> None:
    assert _format_bytes(5000 * 1024**4).endswith(" TB")


# --- render_publication ------------------------------------------------------


def _article_payload() -> dict:
    # Names and ids are kept short so no content line wraps at the test
    # console's 80 columns — the asserts are on contiguous substrings.
    return {
        "title": "Attention Is All You Need",
        "authors": [
            {"name": "A Uno"},
            {"name": "B Dos"},
            {"name": "C Tre"},
            {"name": "D Six"},
            {"name": "E Ott"},
            {"name": "F Net"},
        ],
        "year": 2017,
        "venue": "NeurIPS",
        "doi": "10.1234/attn",
        "pdf_url": "https://arxiv.org/pdf/1706.03762",
        "citation_key": "UnoAl2017",
        "kind": "article",
        "abstract": "The dominant sequence transduction models...",
    }


def test_render_publication_article_rich(capsys) -> None:
    render_publication(_article_payload(), mode=RICH)
    out = capsys.readouterr().out
    assert "Attention Is All You Need" in out
    assert "A Uno" in out
    assert "(+1 more)" in out  # six authors, five shown
    assert "2017" in out
    assert "NeurIPS" in out
    assert "doi:10.1234/attn" in out
    assert "cite:UnoAl2017" in out
    assert "1706.03762" in out  # the PDF line
    assert "Abstract" in out
    assert "dominant sequence transduction" in out


def test_render_publication_article_without_pdf_warns(capsys) -> None:
    payload = _article_payload()
    payload["pdf_url"] = None
    payload["abstract"] = None
    render_publication(payload, mode=RICH)
    out = capsys.readouterr().out
    assert "no PDF found" in out
    assert "Abstract" not in out


def test_render_publication_book_rich(capsys) -> None:
    payload = {
        "title": "Cannibal Capitalism",
        "authors": [{"name": "Nancy Fraser"}],
        "year": 2022,
        "publisher": "Verso",
        "edition": "1st ed.",
        "isbn_13": "9781839761232",
        "citation_key": "Fraser2022",
        "kind": "book",
        "pdf_url": None,
        "abstract": "How our system is devouring democracy.",
    }
    render_publication(payload, mode=RICH)
    out = capsys.readouterr().out
    assert "Cannibal Capitalism" in out
    assert "Nancy Fraser" in out
    assert "Verso" in out
    assert "1st ed." in out
    assert "isbn:9781839761232" in out
    # Books are not expected to carry an OA PDF — no nag line.
    assert "no PDF found" not in out
    # Book prose is labelled Description, not Abstract.
    assert "Description" in out
    assert "Abstract" not in out


def test_render_publication_minimal_payload(capsys) -> None:
    """Missing fields degrade to placeholders instead of crashing."""
    render_publication({}, mode=RICH)
    out = capsys.readouterr().out
    assert "(no title)" in out
    assert "no PDF found" in out


def test_render_publication_json_mode(capsys) -> None:
    payload = _article_payload()
    render_publication(payload, mode=OutputMode(json=True))
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == payload


# --- render_search -----------------------------------------------------------


def test_render_search_multiple_hits_with_edge_cases(capsys) -> None:
    """Two hits: a non-resolvable id with >3 authors, then a bare hit
    with no authors, no year, no id, and no type."""
    payload = {
        "hits": [
            {
                "rank": 1,
                "title": "First Hit",
                "authors": [
                    {"name": "Alpha One"},
                    {"name": "Beta Two"},
                    {"name": "Gamma Three"},
                    {"name": "Delta Four"},
                ],
                "year": 2020,
                "type": "article",
                "id": "openalex:W123",
                "id_resolvable": False,
                "sources": ["openalex", "crossref"],
            },
            {
                "rank": 2,
                "title": "Second Hit",
                "authors": [],
            },
        ],
    }
    render_search(payload, mode=RICH)
    out = capsys.readouterr().out
    assert "First Hit" in out
    assert "Alpha One" in out
    assert "(+1)" in out  # four authors, three shown
    assert "openalex:W123" in out
    assert "(not accepted by quelle fetch)" in out
    assert "openalex, crossref" in out
    assert "Second Hit" in out
    assert "no identifier" in out
    assert "unknown" in out  # missing type degrades to "unknown"


def test_render_search_empty_hits(capsys) -> None:
    render_search({"hits": []}, mode=RICH)
    assert "no matches" in capsys.readouterr().out


# --- render_cache_list -------------------------------------------------------


def test_render_cache_list_with_entries(capsys) -> None:
    payload = {
        "total": 2,
        "schema_version": 3,
        "newest_cached_at": "2026-06-09T10:00:00+00:00",
        "oldest_cached_at": "2026-06-01T09:00:00+00:00",
        "size_bytes": 2048,
        "entries": [
            {
                "citation_key": "Vaswani2017",
                "doi": "10.1/x",
                "title_key": "attention is all you need",
                "cached_at": "2026-06-09T10:00:00+00:00",
            },
            {
                "citation_key": "Fraser2022",
                "doi": None,
                "title_key": "cannibal capitalism",
                "cached_at": "2026-06-01T09:00:00+00:00",
            },
        ],
    }
    render_cache_list(payload, mode=RICH)
    out = capsys.readouterr().out
    assert "2 entries" in out
    assert "last upsert 2026-06-09T10:00:00" in out
    assert "oldest 2026-06-01T09:00:00" in out
    assert "size 2.0 KB" in out
    assert "schema v3" in out
    assert "Vaswani2017" in out
    assert "10.1/x" in out
    assert "attention is all you" in out
    assert "Fraser2022" in out


def test_render_cache_list_empty(capsys) -> None:
    payload = {
        "total": 0,
        "schema_version": 3,
        "newest_cached_at": None,
        "oldest_cached_at": None,
        "size_bytes": 0,
        "entries": [],
    }
    render_cache_list(payload, mode=RICH)
    out = capsys.readouterr().out
    assert "0 entries" in out
    assert "(empty)" in out
    assert "Citekey" not in out  # no table for an empty cache


def test_render_cache_list_singular_entry_label(capsys) -> None:
    payload = {
        "total": 1,
        "schema_version": 3,
        "newest_cached_at": "2026-06-09T10:00:00+00:00",
        "oldest_cached_at": "2026-06-09T10:00:00+00:00",
        "size_bytes": 100,
        "entries": [
            {
                "citation_key": "Solo2026",
                "doi": None,
                "title_key": "one entry",
                "cached_at": "2026-06-09T10:00:00+00:00",
            }
        ],
    }
    render_cache_list(payload, mode=RICH)
    out = capsys.readouterr().out
    assert "1 entry" in out
    assert "1 entries" not in out
