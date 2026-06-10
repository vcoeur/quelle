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


def _build_v2_cache(db_path: Path) -> None:
    """Hand-build a v2 cache file so the v3 migration path can be exercised.

    Mirrors the v2 schema verbatim — `citation_key TEXT PRIMARY KEY`
    plus the five partial unique identifier indexes — and seeds two
    rows, one carrying a full-URL OpenAlex id as v2 writes stored it.
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
                isbn_10      TEXT,
                isbn_13      TEXT,
                title_key    TEXT,
                payload_json TEXT NOT NULL,
                cached_at    TEXT NOT NULL
            );
            CREATE UNIQUE INDEX publications_doi ON publications(doi)
                WHERE doi IS NOT NULL;
            CREATE UNIQUE INDEX publications_openalex ON publications(openalex_id)
                WHERE openalex_id IS NOT NULL;
            CREATE UNIQUE INDEX publications_arxiv ON publications(arxiv_id)
                WHERE arxiv_id IS NOT NULL;
            CREATE UNIQUE INDEX publications_isbn_13 ON publications(isbn_13)
                WHERE isbn_13 IS NOT NULL;
            CREATE UNIQUE INDEX publications_isbn_10 ON publications(isbn_10)
                WHERE isbn_10 IS NOT NULL;
            CREATE INDEX publications_title ON publications(title_key);
            """
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", "2"),
        )
        rows = [
            (
                "ChanVese2001",
                "10.1109/83.902291",
                "https://openalex.org/W2148263991",
                None,
                None,
                None,
                "active contours without edges",
                (
                    '{"title": "Active contours without edges", "authors": [], '
                    '"resolved_from_chain": ["openalex"]}'
                ),
                "2026-01-01T00:00:00+00:00",
            ),
            (
                "Vaswani2017",
                None,
                None,
                "1706.03762",
                None,
                None,
                "attention is all you need",
                (
                    '{"title": "Attention Is All You Need", "authors": [], '
                    '"resolved_from_chain": ["arxiv"]}'
                ),
                "2026-01-02T00:00:00+00:00",
            ),
        ]
        connection.executemany(
            "INSERT INTO publications "
            "(citation_key, doi, openalex_id, arxiv_id, isbn_10, isbn_13, "
            " title_key, payload_json, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def test_open_migrates_v2_cache_to_surrogate_id(tmp_path: Path) -> None:
    """Opening a v2 cache must rebuild it around the surrogate-id key.

    Every row must survive the recreate-and-copy, full-URL OpenAlex ids
    must be normalised to the bare form, and citation_key must stop
    being the primary key (so colliding keys no longer destroy rows).
    """
    db_path = tmp_path / "cache.sqlite"
    _build_v2_cache(db_path)

    with Cache.open(db_path) as cache:
        assert cache.stats()["total"] == 2
        assert cache.stats()["schema_version"] == SCHEMA_VERSION
        hit = cache.get_by_doi("10.1109/83.902291")
        assert hit is not None and hit.title == "Active contours without edges"
        assert cache.get_by_arxiv_id("1706.03762") is not None
        # Full-URL OpenAlex id was normalised and is now findable.
        assert cache.get_by_openalex_id("W2148263991") is not None

        # citation_key no longer collides: a different work minting
        # ChanVese2001 coexists with the migrated row.
        cache.upsert(
            Publication(
                title="A different Chan-Vese paper",
                authors=[Author(name="Bob Chan"), Author(name="Carla Vese")],
                year=2001,
                doi="10.9999/other",
            )
        )
        assert cache.stats()["total"] == 3
        assert cache.get_by_doi("10.1109/83.902291") is not None

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(publications)")}
        assert "id" in columns
        stored = connection.execute(
            "SELECT openalex_id FROM publications WHERE doi = '10.1109/83.902291'"
        ).fetchone()
        assert stored[0] == "W2148263991"
    finally:
        connection.close()


def test_upsert_same_work_under_new_citation_key_updates_row(cache: Cache) -> None:
    """Re-resolving the same work under a different key must not crash.

    Under the v2 schema this hit the unique DOI index (IntegrityError);
    now the row is found by identifier and updated in place, key included.
    """
    cache.upsert(_chan_vese())

    from dataclasses import replace

    # Same DOI, but the author list now mints a different citation key.
    rekeyed = replace(
        _chan_vese(),
        authors=[Author(name="Tony F. Chan")],
        abstract="Re-resolved abstract.",
    )
    cache.upsert(rekeyed)

    assert cache.stats()["total"] == 1
    hit = cache.get_by_doi("10.1109/83.902291")
    assert hit is not None
    assert hit.abstract == "Re-resolved abstract."
    entries = cache.list_entries(limit=10)
    assert [e["citation_key"] for e in entries] == ["Chan2001"]


def test_same_citation_key_different_works_both_survive(cache: Cache) -> None:
    """Two distinct works minting the same key coexist as separate rows."""
    first = Publication(
        title="A theory of everything",
        authors=[Author(name="Alice Smith")],
        year=2020,
        doi="10.1000/first",
    )
    second = Publication(
        title="An unrelated result",
        authors=[Author(name="Zed Smith")],
        year=2020,
        doi="10.1000/second",
    )
    assert first.citation_key() == second.citation_key() == "Smith2020"

    cache.upsert(first)
    cache.upsert(second)

    assert cache.stats()["total"] == 2
    assert cache.get_by_doi("10.1000/first") is not None
    assert cache.get_by_doi("10.1000/second") is not None

    # Citation-key reads prefer the most recently cached row...
    hit = cache.get_by_citation_key("Smith2020")
    assert hit is not None and hit.title == "An unrelated result"

    # ...and an update refreshes cached_at, so re-caching the first
    # work makes it the preferred row again.
    cache.upsert(first)
    hit = cache.get_by_citation_key("Smith2020")
    assert hit is not None and hit.title == "A theory of everything"
    assert cache.stats()["total"] == 2


def test_upsert_merges_rows_matched_through_different_identifiers(cache: Cache) -> None:
    """A record bridging two partial rows merges them into one.

    One row known by DOI only, one by arXiv id only; a fully-enriched
    record carrying both must update one and absorb the other instead
    of tripping a unique identifier index.
    """
    cache.upsert(
        Publication(
            title="Attention Is All You Need",
            authors=[Author(name="Ashish Vaswani")],
            year=2017,
            doi="10.5555/3295222",
        )
    )
    cache.upsert(_vaswani())  # arXiv id only
    assert cache.stats()["total"] == 2

    from dataclasses import replace

    enriched = replace(_vaswani(), doi="10.5555/3295222")
    cache.upsert(enriched)

    assert cache.stats()["total"] == 1
    hit = cache.get_by_doi("10.5555/3295222")
    assert hit is not None
    assert hit.arxiv_id == "1706.03762"


def test_upsert_identifierless_same_title_does_not_duplicate(cache: Cache) -> None:
    """Identifier-less records dedupe on citation key + exact title."""
    perceptron = Publication(
        title="The Perceptron",
        authors=[Author(name="Frank Rosenblatt")],
        year=1958,
    )
    cache.upsert(perceptron)
    cache.upsert(perceptron)
    assert cache.stats()["total"] == 1


def test_openalex_lookup_hits_both_query_and_stored_forms(cache: Cache) -> None:
    """`openalex:` / URL / bare-id queries all hit, against either stored form."""
    cache.upsert(_chan_vese())  # openalex_id given in full-URL form

    assert cache.get_by_openalex_id("W2148263991") is not None
    assert cache.lookup("openalex:W2148263991") is not None
    assert cache.lookup("https://openalex.org/W2148263991") is not None
    assert cache.lookup("openalex:W0000000") is None

    # Rows written before v3 stored the full-URL form — reads must
    # still hit those (simulated via a raw insert).
    cache._conn.execute(
        "INSERT INTO publications "
        "(citation_key, openalex_id, title_key, payload_json, cached_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "Old2019",
            "https://openalex.org/W999",
            "an old row",
            '{"title": "An old row", "authors": []}',
            "2026-01-01T00:00:00+00:00",
        ),
    )
    cache._conn.commit()
    assert cache.get_by_openalex_id("W999") is not None
    assert cache.lookup("openalex:W999") is not None


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
