"""Tests for the SQLite publication cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from quelle.models.publication import Author, Publication
from quelle.repositories.cache import Cache, _title_key


def _chan_vese() -> Publication:
    return Publication(
        title="Active contours without edges",
        authors=[Author(name="Tony F. Chan"), Author(name="Luminita A. Vese")],
        year=2001,
        venue="IEEE Transactions on Image Processing",
        doi="10.1109/83.902291",
        openalex_id="https://openalex.org/W2148263991",
        abstract="Region-based active contour model.",
        citation_count=15000,
        resolved_from_chain=["openalex", "crossref"],
    )


def _vaswani() -> Publication:
    return Publication(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani")],
        year=2017,
        arxiv_id="1706.03762",
        abstract="The dominant sequence transduction models...",
        is_open_access=True,
        pdf_url="https://arxiv.org/pdf/1706.03762",
        resolved_from_chain=["arxiv"],
    )


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    with Cache.open(tmp_path / ".publications-state" / "cache.sqlite") as c:
        yield c


def test_open_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / ".publications-state" / "cache.sqlite"
    with Cache.open(db):
        pass
    # Second open must not error or reset state.
    with Cache.open(db) as cache:
        assert cache.stats()["total"] == 0


def test_upsert_then_get_by_doi_roundtrip(cache: Cache) -> None:
    publication = _chan_vese()
    cache.upsert(publication)

    retrieved = cache.get_by_doi("10.1109/83.902291")
    assert retrieved is not None
    assert retrieved.title == publication.title
    assert len(retrieved.authors) == 2
    assert retrieved.authors[0].name == "Tony F. Chan"
    assert retrieved.abstract == publication.abstract
    assert retrieved.resolved_from_chain == ["openalex", "crossref"]


def test_upsert_indexes_all_ids(cache: Cache) -> None:
    publication = _vaswani()
    cache.upsert(publication)

    assert cache.get_by_arxiv_id("1706.03762") is not None
    # Title lookup works too (title_key is lowercased + whitespace-collapsed).
    assert cache.get_by_title_exact("attention is all you need") is not None
    # DOI not set on this publication.
    assert cache.get_by_doi("10.fake/doi") is None


def test_upsert_replaces_existing_row(cache: Cache) -> None:
    first = _chan_vese()
    cache.upsert(first)

    # Construct a second row with same citation key + DOI but updated abstract.
    from dataclasses import replace

    updated = replace(first, abstract="Updated abstract.")
    cache.upsert(updated)

    retrieved = cache.get_by_doi("10.1109/83.902291")
    assert retrieved is not None
    assert retrieved.abstract == "Updated abstract."
    assert cache.stats()["total"] == 1


def test_get_by_doi_case_insensitive(cache: Cache) -> None:
    cache.upsert(_chan_vese())
    assert cache.get_by_doi("10.1109/83.902291") is not None
    assert cache.get_by_doi("10.1109/83.902291".upper()) is not None


def test_clear_removes_everything(cache: Cache) -> None:
    cache.upsert(_chan_vese())
    cache.upsert(_vaswani())
    assert cache.stats()["total"] == 2

    removed = cache.clear()
    assert removed == 2
    assert cache.stats()["total"] == 0
    assert cache.get_by_doi("10.1109/83.902291") is None


def test_list_entries_returns_most_recent_first(cache: Cache) -> None:
    cache.upsert(_chan_vese())
    cache.upsert(_vaswani())
    entries = cache.list_entries(limit=10)
    assert len(entries) == 2
    assert {e["citation_key"] for e in entries} == {"ChanVese2001", "Vaswani2017"}


def test_get_by_title_exact_miss(cache: Cache) -> None:
    assert cache.get_by_title_exact("unknown paper") is None


def test_title_key_normalises_whitespace_and_case() -> None:
    assert _title_key("  Hello   World  ") == "hello world"
    assert _title_key("") == ""
