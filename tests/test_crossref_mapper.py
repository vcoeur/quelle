"""Unit tests for the Crossref -> Publication mapper.

Inlined fixture JSON mirrors the shape of a real
`GET https://api.crossref.org/works/<doi>` response. No network.
"""

from __future__ import annotations

from app.repositories.sources.crossref import (
    _extract_pdf_link,
    _extract_year,
    _strip_jats,
    _to_publication,
)


def _chan_vese_message() -> dict:
    return {
        "DOI": "10.1109/83.902291",
        "title": ["Active contours without edges"],
        "author": [
            {
                "given": "Tony F.",
                "family": "Chan",
                "ORCID": "http://orcid.org/0000-0000-0000-0000",
            },
            {
                "given": "Luminita A.",
                "family": "Vese",
                "affiliation": [{"name": "UCLA"}],
            },
        ],
        "published-print": {"date-parts": [[2001, 2]]},
        "container-title": ["IEEE Transactions on Image Processing"],
        "volume": "10",
        "issue": "2",
        "page": "266-277",
        "publisher": "Institute of Electrical and Electronics Engineers (IEEE)",
        "abstract": (
            "<jats:p>In this paper, we propose a new model for active "
            "contours based on techniques of curve evolution, "
            "Mumford-Shah functional for segmentation and level sets.</jats:p>"
        ),
        "is-referenced-by-count": 15000,
        "URL": "http://dx.doi.org/10.1109/83.902291",
        "subject": ["Computer Graphics and Computer-Aided Design", "Software"],
    }


def test_strip_jats_removes_tags() -> None:
    assert _strip_jats("<jats:p>Hello <b>world</b></jats:p>") == "Hello world"


def test_strip_jats_none_passthrough() -> None:
    assert _strip_jats(None) is None


def test_strip_jats_empty_after_strip() -> None:
    assert _strip_jats("<jats:p></jats:p>") is None


def test_extract_year_from_published_print() -> None:
    assert _extract_year({"published-print": {"date-parts": [[2001, 2]]}}) == 2001


def test_extract_year_falls_back_through_keys() -> None:
    message = {"issued": {"date-parts": [[1999]]}}
    assert _extract_year(message) == 1999


def test_extract_year_none_when_absent() -> None:
    assert _extract_year({}) is None


def test_extract_pdf_link_prefers_application_pdf() -> None:
    message = {
        "link": [
            {"URL": "https://example.com/html", "content-type": "text/html"},
            {"URL": "https://example.com/paper.pdf", "content-type": "application/pdf"},
        ]
    }
    assert _extract_pdf_link(message) == "https://example.com/paper.pdf"


def test_to_publication_maps_chan_vese_message() -> None:
    publication = _to_publication(_chan_vese_message())

    assert publication.title == "Active contours without edges"
    assert publication.year == 2001
    assert publication.doi == "10.1109/83.902291"
    assert publication.venue == "IEEE Transactions on Image Processing"
    assert publication.publisher.startswith("Institute of Electrical")
    assert publication.journal_volume == "10"
    assert publication.journal_issue == "2"
    assert publication.page_range == "266-277"
    assert publication.citation_count == 15000
    assert publication.abstract and publication.abstract.startswith("In this paper")
    assert "<jats:p>" not in (publication.abstract or "")
    assert publication.topics == ["Computer Graphics and Computer-Aided Design", "Software"]
    assert publication.resolved_from_chain == ["crossref"]

    # Two-author citation key derives from both surnames.
    assert publication.citation_key() == "ChanVese2001"
    assert publication.authors[0].orcid.endswith("0000-0000-0000")
    assert publication.authors[1].affiliation == "UCLA"


def test_to_publication_handles_minimal_message() -> None:
    publication = _to_publication({"DOI": "10.1/x"})
    assert publication.title == ""
    assert publication.year is None
    assert publication.doi == "10.1/x"
    assert publication.authors == []
