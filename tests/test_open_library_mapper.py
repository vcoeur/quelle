"""Unit tests for the Open Library -> Publication mappers.

No network: edition / work fixtures are inlined.
"""

from __future__ import annotations

from quelle.models.publication import Author
from quelle.repositories.sources.open_library import (
    _description_text,
    _publish_year,
    _to_publication,
)


def test_publish_year_extracts_trailing_year() -> None:
    assert _publish_year("June 1, 1979") == 1979
    assert _publish_year("1979") == 1979
    assert _publish_year("Spring 2003") == 2003


def test_publish_year_returns_none_on_blank() -> None:
    assert _publish_year(None) is None
    assert _publish_year("") is None
    assert _publish_year("ND") is None


def test_description_text_handles_string_and_object() -> None:
    assert _description_text("flat string") == "flat string"
    assert _description_text({"type": "/type/text", "value": "wrapped"}) == "wrapped"
    assert _description_text(None) is None
    assert _description_text({}) is None


def test_to_publication_maps_edition_with_work() -> None:
    edition = {
        "key": "/books/OL7353617M",
        "title": "Le Rouge et le Noir",
        "publish_date": "1830",
        "publishers": ["Levavasseur"],
        "isbn_10": ["2070407136"],
        "isbn_13": ["9782070407132"],
        "edition_name": "1st ed.",
        "number_of_pages": 564,
        "subjects": ["French literature"],
    }
    work = {
        "title": "Le Rouge et le Noir",
        "description": {"type": "/type/text", "value": "A novel of ambition"},
        "subjects": ["French literature", "Realism"],
    }
    authors = [Author(name="Stendhal")]

    publication = _to_publication(edition, work=work, authors=authors)

    assert publication.title == "Le Rouge et le Noir"
    assert publication.kind == "book"
    assert publication.year == 1830
    assert publication.publisher == "Levavasseur"
    assert publication.isbn_10 == "2070407136"
    assert publication.isbn_13 == "9782070407132"
    assert publication.edition == "1st ed."
    assert publication.page_count == 564
    assert publication.subjects == ["French literature"]
    assert publication.abstract == "A novel of ambition"
    assert publication.source_url == "https://openlibrary.org/books/OL7353617M"
    assert publication.authors == [Author(name="Stendhal")]
    assert publication.resolved_from_chain == ["open_library"]
    assert publication.citation_key() == "Stendhal1830"


def test_to_publication_falls_back_to_work_subjects() -> None:
    edition = {"key": "/books/OL1M", "title": "X"}
    work = {"subjects": ["Philosophy"]}
    publication = _to_publication(edition, work=work, authors=[])
    assert publication.subjects == ["Philosophy"]


def test_to_publication_handles_missing_work_and_authors() -> None:
    edition = {"key": "/books/OL1M", "title": "Edition only"}
    publication = _to_publication(edition)
    assert publication.title == "Edition only"
    assert publication.authors == []
    assert publication.subjects == []
    assert publication.kind == "book"
