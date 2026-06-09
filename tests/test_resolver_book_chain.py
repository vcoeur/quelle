"""Locks in the book-source priority order.

The resolver tries Open Library → Google Books → BnF → OpenAlex (in
that exact order, for the reasons documented in `_book_sources`).
These tests monkeypatch each source's `fetch_by_isbn` so we can
assert which sources are called, in what order, and that a
downstream success after upstream misses still returns the right
Publication.
"""

from __future__ import annotations

import httpx
import pytest

from quelle.models.publication import Author, Publication
from quelle.repositories.errors import NotFoundError
from quelle.repositories.sources import bnf, google_books, open_library, openalex
from quelle.services.resolver import _book_record_complete, _enrich_book, resolve_book_primary
from quelle.settings import Settings


def _book(source: str) -> Publication:
    """Helper: a minimal book Publication tagged with its source."""
    return Publication(
        title=f"From {source}",
        kind="book",
        isbn_13="9780000000002",
        resolved_from_chain=[source],
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Tracks the order in which each book source's fetch_by_isbn is invoked."""
    invoked: list[str] = []

    def make_stub(source_name: str, *, raises: bool):
        def stub(client, settings, isbn):
            del client, settings, isbn
            invoked.append(source_name)
            if raises:
                raise NotFoundError(f"{source_name}: no record")
            return _book(source_name)

        return stub

    # Default: every source raises NotFoundError. Individual tests
    # override the ones they want to succeed.
    monkeypatch.setattr(open_library, "fetch_by_isbn", make_stub("open_library", raises=True))
    monkeypatch.setattr(google_books, "fetch_by_isbn", make_stub("google_books", raises=True))
    monkeypatch.setattr(bnf, "fetch_by_isbn", make_stub("bnf", raises=True))
    monkeypatch.setattr(openalex, "fetch_by_isbn", make_stub("openalex", raises=True))
    return invoked


@pytest.fixture
def fake_client() -> httpx.Client:
    return httpx.Client()


def test_open_library_is_tried_first(
    calls: list[str],
    fake_client: httpx.Client,
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Open Library succeeds, no other source must be called."""

    def stub_open_library(client, settings, isbn):
        del client, settings, isbn
        calls.append("open_library")
        return _book("open_library")

    monkeypatch.setattr(open_library, "fetch_by_isbn", stub_open_library)

    publication = resolve_book_primary(fake_client, tmp_settings, "9780000000002")
    assert publication.resolved_from_chain == ["open_library"]
    assert calls == ["open_library"]


def test_falls_through_open_library_to_google_books(
    calls: list[str],
    fake_client: httpx.Client,
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stub_google_books(client, settings, isbn):
        del client, settings, isbn
        calls.append("google_books")
        return _book("google_books")

    monkeypatch.setattr(google_books, "fetch_by_isbn", stub_google_books)

    publication = resolve_book_primary(fake_client, tmp_settings, "9780000000002")
    assert publication.resolved_from_chain == ["google_books"]
    assert calls == ["open_library", "google_books"]


def test_falls_through_to_bnf_when_first_two_miss(
    calls: list[str],
    fake_client: httpx.Client,
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stub_bnf(client, settings, isbn):
        del client, settings, isbn
        calls.append("bnf")
        return _book("bnf")

    monkeypatch.setattr(bnf, "fetch_by_isbn", stub_bnf)

    publication = resolve_book_primary(fake_client, tmp_settings, "9780000000002")
    assert publication.resolved_from_chain == ["bnf"]
    assert calls == ["open_library", "google_books", "bnf"]


def test_openalex_is_last_resort(
    calls: list[str],
    fake_client: httpx.Client,
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAlex is fourth in the chain because its ISBN search is prone
    to false positives — first three sources are exhausted before we ask it."""

    def stub_openalex(client, settings, isbn):
        del client, settings, isbn
        calls.append("openalex")
        return _book("openalex")

    monkeypatch.setattr(openalex, "fetch_by_isbn", stub_openalex)

    publication = resolve_book_primary(fake_client, tmp_settings, "9780000000002")
    assert publication.resolved_from_chain == ["openalex"]
    assert calls == ["open_library", "google_books", "bnf", "openalex"]


def test_all_sources_miss_raises_last_error(
    calls: list[str], fake_client: httpx.Client, tmp_settings: Settings
) -> None:
    """Every source raises NotFoundError → the last error bubbles up."""
    with pytest.raises(NotFoundError, match="openalex"):
        resolve_book_primary(fake_client, tmp_settings, "9780000000002")
    assert calls == ["open_library", "google_books", "bnf", "openalex"]


def _complete_book(source: str, *, authors: list[Author]) -> Publication:
    """A book record that is 'complete' except possibly for its authors."""
    return Publication(
        title="Radical Candor",
        kind="book",
        isbn_13="9780000000002",
        authors=authors,
        publisher="St. Martin's Press",
        year=2019,
        subjects=["management"],
        resolved_from_chain=[source],
    )


def test_record_incomplete_without_authors() -> None:
    """A book missing only its authors is not 'complete' — enrichment continues."""
    assert not _book_record_complete(_complete_book("open_library", authors=[]))
    assert _book_record_complete(_complete_book("open_library", authors=[Author(name="Kim Scott")]))


def test_enrich_book_backfills_authors_from_secondary(
    fake_client: httpx.Client,
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary source has everything but authors; enrichment walks on to the
    source that carries the author, so the CiteKey ends up author-based."""
    primary = _complete_book("open_library", authors=[])

    def google_with_author(client, settings, isbn):
        del client, settings, isbn
        return Publication(
            title="Radical Candor",
            kind="book",
            isbn_13="9780000000002",
            authors=[Author(name="Kim Scott")],
            resolved_from_chain=["google_books"],
        )

    def raise_not_found(name: str):
        def stub(client, settings, isbn):
            del client, settings, isbn
            raise NotFoundError(f"{name}: no record")

        return stub

    monkeypatch.setattr(google_books, "fetch_by_isbn", google_with_author)
    monkeypatch.setattr(bnf, "fetch_by_isbn", raise_not_found("bnf"))
    monkeypatch.setattr(openalex, "fetch_by_isbn", raise_not_found("openalex"))

    enriched = _enrich_book(fake_client, tmp_settings, primary)

    assert [a.name for a in enriched.authors] == ["Kim Scott"]
    assert enriched.citation_key() == "Scott2019"


def test_enrich_book_skips_every_source_already_in_chain(
    fake_client: httpx.Client,
    tmp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record already merged from several sources must not re-query them —
    only the sources absent from `resolved_from_chain` are consulted."""
    invoked: list[str] = []

    def make_stub(source_name: str):
        def stub(client, settings, isbn):
            del client, settings, isbn
            invoked.append(source_name)
            raise NotFoundError(f"{source_name}: no record")

        return stub

    monkeypatch.setattr(open_library, "fetch_by_isbn", make_stub("open_library"))
    monkeypatch.setattr(google_books, "fetch_by_isbn", make_stub("google_books"))
    monkeypatch.setattr(bnf, "fetch_by_isbn", make_stub("bnf"))
    monkeypatch.setattr(openalex, "fetch_by_isbn", make_stub("openalex"))

    incomplete = Publication(
        title="Radical Candor",
        kind="book",
        isbn_13="9780000000002",
        resolved_from_chain=["open_library", "google_books"],
    )
    _enrich_book(fake_client, tmp_settings, incomplete)

    assert invoked == ["bnf", "openalex"]
