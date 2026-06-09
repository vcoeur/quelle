"""Unit tests for the OpenAlex -> Publication mapper.

No network: the raw work dicts are inlined fixtures mirroring the
shape of `GET /works/...` responses.
"""

from __future__ import annotations

import httpx
import pytest

from quelle.repositories.errors import NetworkError, NotFoundError
from quelle.repositories.sources import openalex
from quelle.repositories.sources.openalex import (
    _extract_arxiv_id,
    _reconstruct_abstract,
    _to_publication,
)


def test_reconstruct_abstract_orders_words_by_position() -> None:
    inverted = {"Hello": [0, 3], "world": [1], "again": [2]}
    assert _reconstruct_abstract(inverted) == "Hello world again Hello"


def test_reconstruct_abstract_handles_none() -> None:
    assert _reconstruct_abstract(None) is None


def test_reconstruct_abstract_handles_empty() -> None:
    assert _reconstruct_abstract({}) is None


def test_to_publication_maps_minimal_work() -> None:
    work = {
        "title": "Attention is all you need",
        "publication_year": 2017,
        "doi": "https://doi.org/10.48550/arxiv.1706.03762",
        "cited_by_count": 100000,
        "open_access": {"is_oa": True},
        "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1706.03762"},
        "authorships": [
            {
                "author": {"display_name": "Ashish Vaswani"},
                "institutions": [{"display_name": "Google"}],
            },
        ],
        "primary_location": {"landing_page_url": "https://arxiv.org/abs/1706.03762"},
        "id": "https://openalex.org/W1234",
        "topics": [{"display_name": "Deep Learning"}, {"display_name": "Transformers"}],
    }
    publication = _to_publication(work)

    assert publication.title == "Attention is all you need"
    assert publication.year == 2017
    assert publication.doi == "10.48550/arxiv.1706.03762"
    assert publication.is_open_access is True
    assert publication.pdf_url == "https://arxiv.org/pdf/1706.03762"
    assert publication.authors[0].name == "Ashish Vaswani"
    assert publication.authors[0].affiliation == "Google"
    assert publication.citation_count == 100000
    assert publication.resolved_from_chain == ["openalex"]
    assert publication.topics == ["Deep Learning", "Transformers"]
    assert publication.citation_key() == "Vaswani2017"


def test_to_publication_skips_authors_without_name() -> None:
    work = {
        "title": "Anonymous paper",
        "authorships": [
            {"author": {}, "institutions": []},
            {"author": {"display_name": "Real Person"}, "institutions": []},
        ],
    }
    publication = _to_publication(work)
    assert [author.name for author in publication.authors] == ["Real Person"]


def test_to_publication_skips_whitespace_only_author_names() -> None:
    # Regression: a " " display_name used to pass the empty-name guard and
    # later crash Publication.citation_key().
    work = {
        "title": "Garbage author paper",
        "publication_year": 2020,
        "authorships": [
            {"author": {"display_name": "   "}, "institutions": []},
            {"author": {"display_name": "Real Person"}, "institutions": []},
        ],
    }
    publication = _to_publication(work)
    assert [author.name for author in publication.authors] == ["Real Person"]
    assert publication.citation_key() == "Person2020"


def test_to_publication_missing_title_yields_empty_string() -> None:
    publication = _to_publication({"publication_year": 2020})
    assert publication.title == ""
    assert publication.year == 2020
    assert publication.citation_key() == "Unknown2020"


def test_extract_arxiv_id_from_landing_url() -> None:
    work = {"locations": [{"landing_page_url": "https://arxiv.org/abs/1706.03762"}]}
    assert _extract_arxiv_id(work) == "1706.03762"


def test_extract_arxiv_id_from_pdf_url() -> None:
    work = {"locations": [{"pdf_url": "https://arxiv.org/pdf/2301.12345.pdf"}]}
    assert _extract_arxiv_id(work) == "2301.12345"


def test_extract_arxiv_id_returns_none_when_absent() -> None:
    work = {"locations": [{"landing_page_url": "https://example.com/paper"}]}
    assert _extract_arxiv_id(work) is None


def test_to_publication_tags_book_kind() -> None:
    work = {
        "title": "Archives du Nord",
        "type": "book",
        "publication_year": 1977,
        "primary_location": {"source": {"display_name": "Gallimard"}},
    }
    publication = _to_publication(work)
    assert publication.kind == "book"


def test_to_publication_tags_article_kind() -> None:
    work = {"title": "Some paper", "type": "article"}
    publication = _to_publication(work)
    assert publication.kind == "article"


def test_to_publication_drops_unknown_kind() -> None:
    work = {"title": "An entry", "type": "dataset"}
    publication = _to_publication(work)
    assert publication.kind is None


# --- Wire-level error mapping ---------------------------------------------


def test_fetch_by_doi_maps_404_to_not_found(httpx_mock, tmp_settings) -> None:
    """An unknown DOI is a not-found (exit 1), not a network failure (exit 2)."""
    httpx_mock.add_response(
        url="https://api.openalex.org/works/doi:10.1234/missing?mailto=tests%40example.com",
        status_code=404,
        json={"error": "Not Found"},
    )
    with httpx.Client() as client, pytest.raises(NotFoundError, match="10.1234/missing"):
        openalex.fetch_by_doi(client, tmp_settings, "10.1234/missing")


def test_fetch_by_doi_keeps_5xx_as_network_error(httpx_mock, tmp_settings) -> None:
    httpx_mock.add_response(
        url="https://api.openalex.org/works/doi:10.1234/flaky?mailto=tests%40example.com",
        status_code=500,
        text="boom",
    )
    with httpx.Client() as client, pytest.raises(NetworkError):
        openalex.fetch_by_doi(client, tmp_settings, "10.1234/flaky")


def test_fetch_by_doi_percent_encodes_doi(httpx_mock, tmp_settings) -> None:
    """`?` / `#` in a DOI must not truncate the path or inject query params."""
    httpx_mock.add_response(
        url=("https://api.openalex.org/works/doi:10.1000/weird%3Fx%23y?mailto=tests%40example.com"),
        json={"title": "Odd DOI", "type": "article"},
    )
    with httpx.Client() as client:
        publication = openalex.fetch_by_doi(client, tmp_settings, "10.1000/weird?x#y")
    assert publication.title == "Odd DOI"
