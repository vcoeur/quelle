"""Unit tests for the Semantic Scholar -> Publication mapper."""

from __future__ import annotations

import pytest

from quelle.repositories.errors import NotFoundError
from quelle.repositories.sources.semantic_scholar import _to_publication


def _yang_paper() -> dict:
    return {
        "paperId": "abc123deadbeef",
        "externalIds": {
            "DOI": "10.1016/j.neucom.2022.02.079",
            "ArXiv": None,
        },
        "title": "An overview of edge and object contour detection",
        "abstract": "Edge and contour detection are fundamental tasks in computer vision.",
        "year": 2022,
        "authors": [
            {"name": "Daipeng Yang", "affiliations": ["Southwest Jiaotong University"]},
            {"name": "Bo Peng", "affiliations": []},
            {"name": "Zaid Al-Huda", "affiliations": []},
            {"name": "Asad Malik", "affiliations": []},
            {"name": "Donghai Zhai", "affiliations": []},
        ],
        "publicationVenue": {
            "name": "Neurocomputing",
            "publisher": "Elsevier",
        },
        "citationCount": 42,
        "openAccessPdf": {"url": "https://example.com/yang2022.pdf"},
        "url": "https://www.semanticscholar.org/paper/abc123deadbeef",
        "fieldsOfStudy": ["Computer Science"],
    }


def test_to_publication_maps_yang_paper() -> None:
    publication = _to_publication(_yang_paper())

    assert publication.title == "An overview of edge and object contour detection"
    assert publication.year == 2022
    assert publication.doi == "10.1016/j.neucom.2022.02.079"
    assert publication.semantic_scholar_id == "abc123deadbeef"
    assert publication.venue == "Neurocomputing"
    assert publication.publisher == "Elsevier"
    assert publication.abstract and publication.abstract.startswith("Edge and contour")
    assert publication.citation_count == 42
    assert publication.is_open_access is True
    assert publication.pdf_url == "https://example.com/yang2022.pdf"
    assert publication.topics == ["Computer Science"]
    assert publication.resolved_from_chain == ["semantic_scholar"]
    # 5 authors -> Al suffix
    assert publication.citation_key() == "YangAl2022"
    assert publication.authors[0].affiliation == "Southwest Jiaotong University"


def test_to_publication_raises_on_error_payload() -> None:
    with pytest.raises(NotFoundError):
        _to_publication({"error": "Paper not found"})


def test_to_publication_raises_on_empty_payload() -> None:
    with pytest.raises(NotFoundError):
        _to_publication({})


def test_to_publication_without_pdf_sets_oa_false() -> None:
    paper = {
        "paperId": "x",
        "title": "No OA copy",
        "openAccessPdf": {},
    }
    publication = _to_publication(paper)
    assert publication.pdf_url is None
    assert publication.is_open_access is False
