"""Unit tests for the BnF SRU -> Publication mapper.

No network: SRU XML envelopes are inlined and parsed directly.
"""

from __future__ import annotations

import pytest

from quelle.repositories.errors import NotFoundError
from quelle.repositories.sources.bnf import (
    _clean_creator,
    _extract_identifiers,
    _extract_year,
    _first_record,
    _to_publication,
)


def test_clean_creator_strips_life_dates() -> None:
    assert _clean_creator("Stendhal (1783-1842)") == "Stendhal"
    assert _clean_creator("Yourcenar, Marguerite (1903-1987)") == "Yourcenar, Marguerite"
    assert _clean_creator("Anonymous") == "Anonymous"


def test_extract_year_picks_four_digit_token() -> None:
    assert _extract_year(["1830"]) == 1830
    assert _extract_year(["impr. 1972"]) == 1972
    assert _extract_year(["sans date"]) is None
    assert _extract_year(None) is None


def test_extract_identifiers_separates_isbn_and_ark() -> None:
    isbn_10, isbn_13, ark = _extract_identifiers(
        [
            "ISBN 2-07-040713-6",
            "ISBN 978-2-07-040713-2",
            "https://catalogue.bnf.fr/ark:/12148/cb12345678f",
        ]
    )
    assert isbn_10 == "2070407136"
    assert isbn_13 == "9782070407132"
    assert ark == "https://catalogue.bnf.fr/ark:/12148/cb12345678f"


def test_first_record_returns_dc_fields() -> None:
    body = """
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
  <srw:numberOfRecords>1</srw:numberOfRecords>
  <srw:records>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Le Rouge et le Noir</dc:title>
          <dc:creator>Stendhal (1783-1842)</dc:creator>
          <dc:publisher>Gallimard</dc:publisher>
          <dc:date>1972</dc:date>
          <dc:subject>Romans français</dc:subject>
          <dc:identifier>ISBN 978-2-07-040713-2</dc:identifier>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>
"""
    fields = _first_record(body, not_found_msg="x")
    assert fields["title"] == ["Le Rouge et le Noir"]
    assert fields["creator"] == ["Stendhal (1783-1842)"]
    assert fields["publisher"] == ["Gallimard"]
    assert fields["date"] == ["1972"]
    assert fields["subject"] == ["Romans français"]
    assert fields["identifier"] == ["ISBN 978-2-07-040713-2"]


def test_first_record_raises_not_found_on_empty_envelope() -> None:
    body = """<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
      <srw:numberOfRecords>0</srw:numberOfRecords>
    </srw:searchRetrieveResponse>"""
    with pytest.raises(NotFoundError):
        _first_record(body, not_found_msg="missing")


def test_to_publication_maps_french_book() -> None:
    record = {
        "title": ["Le Rouge et le Noir"],
        "creator": ["Stendhal (1783-1842)"],
        "publisher": ["Gallimard"],
        "date": ["1972"],
        "subject": ["Romans français", "19e siècle"],
        "identifier": [
            "ISBN 2-07-040713-6",
            "ISBN 978-2-07-040713-2",
            "https://catalogue.bnf.fr/ark:/12148/cb12345678f",
        ],
    }
    publication = _to_publication(record)
    assert publication.title == "Le Rouge et le Noir"
    assert publication.authors[0].name == "Stendhal"
    assert publication.publisher == "Gallimard"
    assert publication.year == 1972
    assert publication.isbn_10 == "2070407136"
    assert publication.isbn_13 == "9782070407132"
    assert publication.subjects == ["Romans français", "19e siècle"]
    assert publication.source_url == "https://catalogue.bnf.fr/ark:/12148/cb12345678f"
    assert publication.kind == "book"
    assert publication.resolved_from_chain == ["bnf"]
    assert publication.citation_key() == "Stendhal1972"
