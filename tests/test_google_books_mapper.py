"""Unit tests for the Google Books -> Publication mapper."""

from __future__ import annotations

from quelle.repositories.sources.google_books import (
    _extract_isbns,
    _publish_year,
    _to_publication,
)


def test_publish_year_extracts_year_prefix() -> None:
    assert _publish_year("2017") == 2017
    assert _publish_year("2017-06") == 2017
    assert _publish_year("2017-06-12") == 2017


def test_publish_year_returns_none_on_blank() -> None:
    assert _publish_year(None) is None
    assert _publish_year("") is None
    assert _publish_year("nd") is None


def test_extract_isbns_picks_both_kinds() -> None:
    identifiers = [
        {"type": "ISBN_10", "identifier": "0140186336"},
        {"type": "ISBN_13", "identifier": "9780140186338"},
    ]
    assert _extract_isbns(identifiers) == ("0140186336", "9780140186338")


def test_extract_isbns_handles_missing_type() -> None:
    assert _extract_isbns([{"identifier": "x"}]) == (None, None)
    assert _extract_isbns([]) == (None, None)


def test_to_publication_maps_full_volume() -> None:
    item = {
        "volumeInfo": {
            "title": "Archives du Nord",
            "authors": ["Marguerite Yourcenar"],
            "publisher": "Gallimard",
            "publishedDate": "1977",
            "pageCount": 384,
            "categories": ["French literature"],
            "description": "Suite du Labyrinthe du monde",
            "industryIdentifiers": [
                {"type": "ISBN_10", "identifier": "2070373282"},
                {"type": "ISBN_13", "identifier": "9782070373284"},
            ],
            "infoLink": "https://books.google.com/books?id=ABC",
        },
        "accessInfo": {
            "accessViewStatus": "SAMPLE",
            "pdf": {"isAvailable": True},
        },
    }

    publication = _to_publication(item)

    assert publication.title == "Archives du Nord"
    assert publication.kind == "book"
    assert publication.year == 1977
    assert publication.publisher == "Gallimard"
    assert publication.page_count == 384
    assert publication.subjects == ["French literature"]
    assert publication.abstract == "Suite du Labyrinthe du monde"
    assert publication.isbn_10 == "2070373282"
    assert publication.isbn_13 == "9782070373284"
    assert publication.authors[0].name == "Marguerite Yourcenar"
    assert publication.source_url == "https://books.google.com/books?id=ABC"
    assert publication.is_open_access is None  # SAMPLE access -> not OA
    assert publication.pdf_url is None
    assert publication.resolved_from_chain == ["google_books"]


def test_to_publication_marks_public_domain_as_open_access() -> None:
    item = {
        "volumeInfo": {"title": "Pride and Prejudice"},
        "accessInfo": {
            "accessViewStatus": "FULL_PUBLIC_DOMAIN",
            "pdf": {"isAvailable": True, "downloadLink": "https://books.google.com/p.pdf"},
        },
    }
    publication = _to_publication(item)
    assert publication.is_open_access is True
    assert publication.pdf_url == "https://books.google.com/p.pdf"


def test_to_publication_handles_minimal_volume() -> None:
    item = {"volumeInfo": {"title": "Bare"}}
    publication = _to_publication(item)
    assert publication.title == "Bare"
    assert publication.authors == []
    assert publication.subjects == []
    assert publication.kind == "book"
