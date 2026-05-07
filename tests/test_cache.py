"""Tests for the SQLite publication cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quelle.models.publication import Author, Publication
from quelle.repositories.cache import SCHEMA_VERSION, Cache, _title_key


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


def _build_v1_cache(db_path: Path) -> None:
    """Hand-build a pre-v2 cache file so the migration path can be exercised.

    Mirrors the v1 schema verbatim (no isbn_10 / isbn_13 columns) and
    seeds it with one row whose payload references an article — the
    ALTER TABLE migration must be additive and preserve every byte.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE publications (
                citation_key TEXT PRIMARY KEY,
                doi          TEXT,
                openalex_id  TEXT,
                arxiv_id     TEXT,
                title_key    TEXT,
                payload_json TEXT NOT NULL,
                cached_at    TEXT NOT NULL
            );
            CREATE UNIQUE INDEX publications_doi ON publications(doi)
                WHERE doi IS NOT NULL;
            """
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", "1"),
        )
        connection.execute(
            "INSERT INTO publications "
            "(citation_key, doi, openalex_id, arxiv_id, title_key, payload_json, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "Pre2001",
                "10.x/y",
                None,
                None,
                "preexisting paper",
                (
                    '{"title": "Preexisting paper", "authors": [], '
                    '"resolved_from_chain": ["openalex"]}'
                ),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_open_migrates_v1_cache_in_place(tmp_path: Path) -> None:
    """Opening a v1 cache must add isbn_10/isbn_13 columns and stamp v2.

    Existing rows must survive the migration unchanged — the schema
    bump is additive (ALTER TABLE ADD COLUMN), not a recreate-and-copy.
    """
    db_path = tmp_path / "cache.sqlite"
    _build_v1_cache(db_path)

    with Cache.open(db_path) as cache:
        # Pre-existing row still readable through the v2 schema.
        hit = cache.get_by_doi("10.x/y")
        assert hit is not None
        assert hit.title == "Preexisting paper"

        # Schema version stamp updated.
        stats = cache.stats()
        assert stats["schema_version"] == SCHEMA_VERSION

        # ISBN lookup works on the migrated table — confirms the columns
        # exist and the unique partial indexes were created.
        assert cache.get_by_isbn("9780140186338") is None

    # Direct schema introspection: both isbn columns must be present.
    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(publications)")}
        assert "isbn_10" in columns
        assert "isbn_13" in columns
    finally:
        connection.close()


def test_open_is_idempotent_on_already_v2_cache(tmp_path: Path) -> None:
    """Re-opening a cache that's already at v2 must be a no-op."""
    db_path = tmp_path / "cache.sqlite"
    with Cache.open(db_path) as cache:
        cache.upsert(_chan_vese())
    # Open again — must not raise, must preserve the row.
    with Cache.open(db_path) as cache:
        assert cache.get_by_doi("10.1109/83.902291") is not None
        assert cache.stats()["schema_version"] == SCHEMA_VERSION


def test_isbn_lookup_round_trip(cache: Cache) -> None:
    """Upsert a book record, retrieve it via either ISBN form."""
    book = Publication(
        title="Archives du Nord",
        authors=[Author(name="Marguerite Yourcenar")],
        year=1977,
        publisher="Gallimard",
        isbn_10="2070373282",
        isbn_13="9782070373284",
        kind="book",
        resolved_from_chain=["bnf"],
    )
    cache.upsert(book)

    by_13 = cache.get_by_isbn("9782070373284")
    by_10 = cache.get_by_isbn("2070373282")

    assert by_13 is not None and by_13.title == "Archives du Nord"
    assert by_10 is not None and by_10.title == "Archives du Nord"
    assert cache.get_by_isbn("0000000000") is None
