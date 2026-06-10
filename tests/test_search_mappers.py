"""Per-source `_to_search_hit` mapper tests.

Each adapter's `search()` calls a private `_to_search_hit` mapper to
turn a raw API response shape into a `SearchHit`. These tests exercise
the mappers directly with inlined fixtures — no network.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from quelle.repositories.sources import (
    arxiv,
    bnf,
    google_books,
    open_library,
    openalex,
    semantic_scholar,
)


def test_openalex_search_hit_classifies_book() -> None:
    work = {
        "title": "Archives du Nord",
        "type": "book",
        "publication_year": 1977,
        "id": "https://openalex.org/W42",
        "authorships": [{"author": {"display_name": "Marguerite Yourcenar"}}],
    }
    hit = openalex._to_search_hit(work, rank=2)
    assert hit.title == "Archives du Nord"
    assert hit.year == 1977
    assert hit.type == "book"
    assert hit.source == "openalex"
    assert hit.source_id == "https://openalex.org/W42"
    assert hit.raw_rank == 2
    assert hit.authors[0].name == "Marguerite Yourcenar"


def test_openalex_search_hit_classifies_article_with_doi() -> None:
    work = {
        "title": "Attention is all you need",
        "type": "article",
        "publication_year": 2017,
        "doi": "https://doi.org/10.48550/arXiv.1706.03762",
        "id": "https://openalex.org/W7",
        "locations": [{"landing_page_url": "https://arxiv.org/abs/1706.03762"}],
    }
    hit = openalex._to_search_hit(work, rank=0)
    assert hit.type == "article"
    assert hit.doi == "10.48550/arxiv.1706.03762"
    assert hit.arxiv_id == "1706.03762"


def test_semantic_scholar_search_hit_extracts_external_ids() -> None:
    paper = {
        "paperId": "abc123",
        "title": "Some paper",
        "year": 2020,
        "authors": [{"name": "Jane Doe"}],
        "externalIds": {"DOI": "10.1234/Foo", "ArXiv": "2001.00001"},
    }
    hit = semantic_scholar._to_search_hit(paper, rank=1)
    assert hit.title == "Some paper"
    assert hit.year == 2020
    assert hit.type == "article"
    assert hit.doi == "10.1234/foo"
    assert hit.arxiv_id == "2001.00001"
    assert hit.source == "semantic_scholar"
    assert hit.source_id == "abc123"


def test_arxiv_search_hit_pulls_id_and_year_from_atom() -> None:
    body = """\
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v5</id>
    <title>Attention Is All You Need</title>
    <published>2017-06-12T00:00:00Z</published>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
  </entry>
</feed>
"""
    root = ET.fromstring(body)
    entry = root.find("{http://www.w3.org/2005/Atom}entry")
    assert entry is not None
    hit = arxiv._to_search_hit(entry, rank=0)
    assert hit.title == "Attention Is All You Need"
    assert hit.year == 2017
    assert hit.type == "article"
    assert hit.arxiv_id == "1706.03762"
    assert hit.source == "arxiv"
    assert [author.name for author in hit.authors] == ["Ashish Vaswani", "Noam Shazeer"]


def test_open_library_search_hit_pulls_isbns_and_year() -> None:
    doc = {
        "title": "L'Étranger",
        "first_publish_year": 1942,
        "author_name": ["Albert Camus"],
        "isbn": ["2070360024", "9782070360024"],
        "key": "/works/OL98765W",
    }
    hit = open_library._to_search_hit(doc, rank=3)
    assert hit.title == "L'Étranger"
    assert hit.year == 1942
    assert hit.type == "book"
    assert hit.isbn_10 == "2070360024"
    assert hit.isbn_13 == "9782070360024"
    assert hit.source == "open_library"
    assert hit.source_id == "/works/OL98765W"


def test_google_books_search_hit_extracts_isbns() -> None:
    item = {
        "id": "vol-id",
        "volumeInfo": {
            "title": "The Stranger",
            "authors": ["Albert Camus"],
            "publishedDate": "1989",
            "industryIdentifiers": [
                {"type": "ISBN_10", "identifier": "0679720200"},
                {"type": "ISBN_13", "identifier": "9780679720201"},
            ],
        },
    }
    hit = google_books._to_search_hit(item, rank=0)
    assert hit.title == "The Stranger"
    assert hit.year == 1989
    assert hit.type == "book"
    assert hit.isbn_10 == "0679720200"
    assert hit.isbn_13 == "9780679720201"


def test_bnf_search_hit_extracts_creator_and_isbn() -> None:
    record = {
        "title": ["L'Étranger"],
        "creator": ["Camus, Albert (1913-1960)"],
        "date": ["impr. 1957"],
        "identifier": [
            "ISBN 2-07-036002-4",
            "9782070360024",
            "http://catalogue.bnf.fr/ark:/12148/cb12345678f",
        ],
    }
    hit = bnf._to_search_hit(record, rank=4)
    assert hit.title == "L'Étranger"
    assert hit.authors[0].name == "Camus, Albert"
    assert hit.year == 1957
    assert hit.type == "book"
    assert hit.isbn_13 == "9782070360024"
    assert hit.source == "bnf"
    assert hit.source_id.startswith("http://catalogue.bnf.fr/ark:")


def test_openalex_search_kind_book_adds_type_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kind="book"` must add `type:book|book-chapter` to the OpenAlex filter param."""
    captured: dict[str, object] = {}

    def fake_get_json(client, url, *, params=None):
        captured["params"] = params or {}
        return {"results": []}

    monkeypatch.setattr(openalex, "get_json", fake_get_json)
    from quelle.settings import Settings

    settings = Settings(
        openalex_api_key="",
        semantic_scholar_api_key="",
        google_books_api_key="",
        unpaywall_email="",
        contact_email="",
        http_timeout=5.0,
        user_agent="test",
        max_pdf_mb=100,
        paths=None,  # type: ignore[arg-type]
    )
    openalex.search(None, settings, "x", kind="book")  # type: ignore[arg-type]
    assert "type:book|book-chapter" in captured["params"].get("filter", "")


def test_openalex_search_kind_article_adds_type_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_json(client, url, *, params=None):
        captured["params"] = params or {}
        return {"results": []}

    monkeypatch.setattr(openalex, "get_json", fake_get_json)
    from quelle.settings import Settings

    settings = Settings(
        openalex_api_key="",
        semantic_scholar_api_key="",
        google_books_api_key="",
        unpaywall_email="",
        contact_email="",
        http_timeout=5.0,
        user_agent="test",
        max_pdf_mb=100,
        paths=None,  # type: ignore[arg-type]
    )
    openalex.search(None, settings, "x", kind="article")  # type: ignore[arg-type]
    assert "type:article|preprint" in captured["params"].get("filter", "")


def test_openalex_search_author_filter_strips_injection_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`,` and `:` in a user-supplied author must not inject extra filters."""
    captured: dict[str, object] = {}

    def fake_get_json(client, url, *, params=None, headers=None):
        captured["params"] = params or {}
        return {"results": []}

    monkeypatch.setattr(openalex, "get_json", fake_get_json)
    from quelle.settings import Settings

    settings = Settings(
        openalex_api_key="",
        semantic_scholar_api_key="",
        google_books_api_key="",
        unpaywall_email="",
        contact_email="",
        http_timeout=5.0,
        user_agent="test",
        max_pdf_mb=100,
        paths=None,  # type: ignore[arg-type]
    )
    openalex.search(None, settings, "x", author="Doe,is_oa:false")  # type: ignore[arg-type]
    filter_value = captured["params"].get("filter", "")
    assert filter_value == "author.display_name.search:Doe is_oa false"


def test_arxiv_parse_feed_list_handles_empty_feed() -> None:
    body = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    assert arxiv._parse_feed_list(body) == []


def test_arxiv_parse_feed_list_raises_on_invalid_xml() -> None:
    from quelle.repositories.errors import NetworkError

    with pytest.raises(NetworkError):
        arxiv._parse_feed_list("not xml")
