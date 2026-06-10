"""Unit tests for the Semantic Scholar -> Publication mapper."""

from __future__ import annotations

import httpx
import pytest

from quelle.repositories.errors import NetworkError, NotFoundError
from quelle.repositories.sources import semantic_scholar
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


def test_to_publication_skips_nameless_authors() -> None:
    paper = {
        "paperId": "x",
        "title": "T",
        "authors": [{"name": ""}, {}, {"name": "Real Name"}],
    }
    publication = _to_publication(paper)
    assert [author.name for author in publication.authors] == ["Real Name"]


def test_to_publication_without_pdf_leaves_oa_unknown() -> None:
    """No openAccessPdf is absence of evidence: a later source must be able
    to set is_open_access, which a sticky False would block under the merge
    contract."""
    paper = {
        "paperId": "x",
        "title": "No OA copy",
        "openAccessPdf": {},
    }
    publication = _to_publication(paper)
    assert publication.pdf_url is None
    assert publication.is_open_access is None


# --- Wire-level behaviour --------------------------------------------------


def test_fetch_by_doi_sends_api_key_header_when_set(httpx_mock, tmp_settings) -> None:
    """SEMANTIC_SCHOLAR_API_KEY must actually reach the wire as `x-api-key`."""
    from dataclasses import replace

    settings = replace(tmp_settings, semantic_scholar_api_key="sekrit")
    httpx_mock.add_response(json=_yang_paper(), match_headers={"x-api-key": "sekrit"})
    with httpx.Client() as client:
        publication = semantic_scholar.fetch_by_doi(
            client, settings, "10.1016/j.neucom.2022.02.079"
        )
    assert publication.title.startswith("An overview")


def test_fetch_by_doi_omits_api_key_header_when_unset(httpx_mock, tmp_settings) -> None:
    httpx_mock.add_response(json=_yang_paper())
    with httpx.Client() as client:
        semantic_scholar.fetch_by_doi(client, tmp_settings, "10.1016/j.neucom.2022.02.079")
    request = httpx_mock.get_request()
    assert request is not None
    assert "x-api-key" not in request.headers


def test_search_sends_api_key_header_when_set(httpx_mock, tmp_settings) -> None:
    from dataclasses import replace

    settings = replace(tmp_settings, semantic_scholar_api_key="sekrit")
    httpx_mock.add_response(json={"data": []}, match_headers={"x-api-key": "sekrit"})
    with httpx.Client() as client:
        assert semantic_scholar.search(client, settings, "edge detection") == []


def test_fetch_by_doi_maps_404_to_not_found(httpx_mock, tmp_settings) -> None:
    """An unknown DOI is a not-found (exit 1), not a network failure (exit 2)."""
    httpx_mock.add_response(status_code=404, text="Paper not found")
    with httpx.Client() as client, pytest.raises(NotFoundError, match="10.1234/missing"):
        semantic_scholar.fetch_by_doi(client, tmp_settings, "10.1234/missing")


def test_fetch_by_doi_propagates_non_404_network_errors(httpx_mock, tmp_settings) -> None:
    """A 5xx is a network failure (exit 2), not a not-found (exit 1)."""
    httpx_mock.add_response(status_code=503, text="upstream down")
    with httpx.Client() as client, pytest.raises(NetworkError) as excinfo:
        semantic_scholar.fetch_by_doi(client, tmp_settings, "10.1/flaky")
    assert excinfo.value.status_code == 503


def test_fetch_by_doi_percent_encodes_doi(httpx_mock, tmp_settings) -> None:
    """`?` / `#` in a DOI must not truncate the path or inject query params."""
    httpx_mock.add_response(json=_yang_paper())
    with httpx.Client() as client:
        semantic_scholar.fetch_by_doi(client, tmp_settings, "10.1000/weird?x#y")
    request = httpx_mock.get_request()
    assert request is not None
    assert "10.1000/weird%3Fx%23y" in str(request.url)
