"""Unit tests for the OpenAlex -> Publication mapper.

No network: the raw work dicts are inlined fixtures mirroring the
shape of `GET /works/...` responses.
"""

from __future__ import annotations

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
