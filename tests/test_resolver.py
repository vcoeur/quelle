"""Unit tests for the resolver's query-shape routing.

The resolver itself is tested end-to-end via pytest-httpx in the CLI
smoke tests. Identifier extraction lives in `quelle._identifiers` and
is tested in `test_identifiers.py`; here we cover the routing built on
top of it, the ISBN backfill, the Google-Scholar-URL rejection path,
and the arXiv→OpenAlex enrichment title gate.
"""

from __future__ import annotations

import httpx
import pytest

from quelle._isbn import isbn10_to_13 as _isbn10_to_13
from quelle._isbn import isbn13_to_10 as _isbn13_to_10
from quelle.models.publication import Publication
from quelle.repositories.errors import UserError
from quelle.services.resolver import (
    _backfill_isbn_pair,
    _enrich_article,
    resolve,
)
from quelle.settings import Settings


def test_isbn10_to_13_known_pairs() -> None:
    # Penguin's "The Solid Mandala" — checked against several public ISBN tools.
    assert _isbn10_to_13("0140186336") == "9780140186338"
    # "Le Rouge et le Noir" Folio Classique edition.
    assert _isbn10_to_13("2070407136") == "9782070407132"


def test_isbn10_to_13_rejects_x_check_digit_input_with_invalid_body() -> None:
    assert _isbn10_to_13("invalid___") is None


def test_isbn13_to_10_known_pairs() -> None:
    assert _isbn13_to_10("9780140186338") == "0140186336"
    assert _isbn13_to_10("9782070407132") == "2070407136"


def test_isbn13_to_10_returns_none_for_979_prefix() -> None:
    # 979 ISBN-13s have no ISBN-10 equivalent.
    assert _isbn13_to_10("9790000000000") is None


def test_backfill_isbn_pair_fills_missing_13() -> None:
    record = Publication(title="x", isbn_10="0140186336", kind="book")
    filled = _backfill_isbn_pair(record)
    assert filled.isbn_10 == "0140186336"
    assert filled.isbn_13 == "9780140186338"


def test_backfill_isbn_pair_fills_missing_10() -> None:
    record = Publication(title="x", isbn_13="9780140186338", kind="book")
    filled = _backfill_isbn_pair(record)
    assert filled.isbn_10 == "0140186336"
    assert filled.isbn_13 == "9780140186338"


def test_backfill_isbn_pair_noop_when_both_present() -> None:
    record = Publication(title="x", isbn_10="0140186336", isbn_13="9780140186338", kind="book")
    assert _backfill_isbn_pair(record) is record


@pytest.mark.parametrize(
    "scholar_url",
    [
        "https://scholar.google.com/scholar?cluster=1234",
        "http://scholar.google.co.uk/citations?user=abc",
        "https://scholar.google.de/scholar?q=attention",
    ],
)
def test_resolve_rejects_google_scholar_urls(scholar_url: str, tmp_settings: Settings) -> None:
    """Scholar has no API; pasting a Scholar URL must surface a clean UserError."""
    client = httpx.Client()
    try:
        with pytest.raises(UserError, match="Google Scholar"):
            resolve(client, tmp_settings, scholar_url)
    finally:
        client.close()


def test_resolve_with_type_hint_delegates_to_search(
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free-text + type_hint=book should pick the top book hit and recurse via ISBN."""
    from quelle.models.publication import Author
    from quelle.models.search import MergedHit
    from quelle.repositories.sources import open_library
    from quelle.services import search as search_service

    captured_search: dict[str, object] = {}
    captured_isbn: dict[str, object] = {}

    def fake_search(client, settings, query, **kwargs):
        captured_search["query"] = query
        captured_search["type"] = kwargs.get("type")
        captured_search["author"] = kwargs.get("author")
        captured_search["limit"] = kwargs.get("limit")
        return [
            MergedHit(
                title="Cannibal Capitalism",
                authors=[Author(name="Nancy Fraser")],
                year=2022,
                type="book",
                isbn_13="9781839761232",
                # Single source → not self-sufficient under the
                # `_hit_is_self_sufficient` predicate, so the resolver
                # still recurses by ISBN through the book chain. See
                # `test_resolve_top_hit_skips_roundtrip_when_corroborated`
                # below for the corroborated-hit short-circuit.
                sources=["open_library"],
            )
        ]

    def fake_isbn_fetch(client, settings, isbn):
        captured_isbn["isbn"] = isbn
        return Publication(title="Cannibal Capitalism", isbn_13=isbn, kind="book")

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(open_library, "fetch_by_isbn", fake_isbn_fetch)

    client = httpx.Client()
    try:
        result = resolve(
            client,
            tmp_settings,
            "Cannibal Capitalism",
            type_hint="book",
            author="fraser",
        )
    finally:
        client.close()

    assert captured_search == {
        "query": "Cannibal Capitalism",
        "type": "book",
        "author": "fraser",
        "limit": 1,
    }
    assert captured_isbn["isbn"] == "9781839761232"
    assert result.title == "Cannibal Capitalism"
    assert result.isbn_13 == "9781839761232"


def test_resolve_with_type_hint_synthesises_when_no_id(
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the top hit has no DOI/ISBN/arXiv id, fall back to a synthesised Publication."""
    from quelle.models.publication import Author
    from quelle.models.search import MergedHit
    from quelle.services import search as search_service

    fake = MergedHit(
        title="Some Obscure Work",
        authors=[Author(name="A. N. Other")],
        year=1999,
        type="book",
        sources=["open_library"],
        source_ids={"open_library": "/works/OL999W"},
    )
    monkeypatch.setattr(search_service, "search", lambda *a, **k: [fake])

    client = httpx.Client()
    try:
        result = resolve(client, tmp_settings, "obscure work", type_hint="book")
    finally:
        client.close()

    assert result.title == "Some Obscure Work"
    assert result.kind == "book"
    assert result.year == 1999
    assert result.resolved_from_chain == ["open_library"]


def test_resolve_explicit_id_ignores_type_hint(
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query that's clearly an ISBN must resolve via the book chain regardless of type_hint."""
    from quelle.repositories.sources import open_library
    from quelle.services import search as search_service

    monkeypatch.setattr(
        search_service,
        "search",
        lambda *a, **k: pytest.fail("search should not be called for explicit ISBN"),
    )
    captured: dict[str, object] = {}

    def fake_isbn_fetch(client, settings, isbn):
        captured["isbn"] = isbn
        return Publication(title="x", isbn_13=isbn, kind="book")

    monkeypatch.setattr(open_library, "fetch_by_isbn", fake_isbn_fetch)

    client = httpx.Client()
    try:
        resolve(client, tmp_settings, "9781839761232", type_hint="article")
    finally:
        client.close()
    assert captured["isbn"] == "9781839761232"


def test_resolve_free_text_with_digit_scatter_goes_to_title_search(
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: "987654321 unix" used to assemble a phantom ISBN-10 and
    mis-route to the book chain; it must fall through to title search."""
    from quelle.repositories.sources import open_library, openalex

    monkeypatch.setattr(
        open_library,
        "fetch_by_isbn",
        lambda *a, **k: pytest.fail("book chain must not be called for free text"),
    )
    captured: dict[str, str] = {}

    def fake_title_search(client, settings, title):
        captured["title"] = title
        return Publication(title=title, kind="article")

    monkeypatch.setattr(openalex, "search_by_title", fake_title_search)

    client = httpx.Client()
    try:
        resolve(client, tmp_settings, "987654321 unix")
    finally:
        client.close()
    assert captured["title"] == "987654321 unix"


@pytest.mark.parametrize("arxiv_id", ["math/0211159", "math.GT/0309136"])
def test_resolve_routes_old_style_arxiv_ids_to_arxiv(
    arxiv_id: str,
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare old-style arXiv ids — with or without subject class — hit arXiv."""
    from quelle.repositories.sources import arxiv

    captured: dict[str, str] = {}

    def fake_fetch(client, settings, query):
        captured["id"] = query
        return Publication(title="x", arxiv_id=query, kind="preprint")

    monkeypatch.setattr(arxiv, "fetch_by_arxiv_id", fake_fetch)

    client = httpx.Client()
    try:
        resolve(client, tmp_settings, arxiv_id)
    finally:
        client.close()
    assert captured["id"] == arxiv_id


def _arxiv_record(title: str) -> Publication:
    return Publication(
        title=title,
        arxiv_id="1706.03762",
        kind="preprint",
        resolved_from_chain=["arxiv"],
    )


def test_enrich_article_skips_openalex_hit_with_mismatching_title(
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the OpenAlex title-search top hit merged unconditionally,
    so a wrong hit's DOI became sticky and drove Crossref/S2 enrichment."""
    from quelle.repositories.sources import openalex

    wrong_hit = Publication(
        title="A survey of completely unrelated metaheuristics",
        doi="10.9999/wrong",
        kind="article",
        resolved_from_chain=["openalex"],
    )
    monkeypatch.setattr(openalex, "search_by_title", lambda *a, **k: wrong_hit)

    client = httpx.Client()
    try:
        enriched = _enrich_article(client, tmp_settings, _arxiv_record("Attention Is All You Need"))
    finally:
        client.close()
    assert enriched.doi is None
    assert "openalex" not in enriched.resolved_from_chain


def test_enrich_article_merges_openalex_hit_with_matching_title(
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A published version whose title matches (modulo case/punctuation) merges."""
    from quelle.repositories.sources import openalex

    published = Publication(
        title="Attention is all you need!",
        doi="10.5555/attention",
        kind="article",
        resolved_from_chain=["openalex"],
    )
    monkeypatch.setattr(openalex, "search_by_title", lambda *a, **k: published)
    # Stop the chain after the title merge — Crossref/S2 are not under test.
    from quelle.repositories.errors import NotFoundError
    from quelle.repositories.sources import crossref, semantic_scholar

    def raise_not_found(*_args, **_kwargs):
        raise NotFoundError("no record")

    monkeypatch.setattr(crossref, "fetch_by_doi", raise_not_found)
    monkeypatch.setattr(semantic_scholar, "fetch_by_doi", raise_not_found)

    client = httpx.Client()
    try:
        enriched = _enrich_article(client, tmp_settings, _arxiv_record("Attention Is All You Need"))
    finally:
        client.close()
    assert enriched.doi == "10.5555/attention"
    assert "openalex" in enriched.resolved_from_chain
