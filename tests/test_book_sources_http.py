"""HTTP-level tests for the book source `fetch_by_isbn` paths.

Mapper tests in `test_open_library_mapper.py`, `test_google_books_mapper.py`,
and `test_bnf_mapper.py` cover the JSON/XML → Publication translation in
isolation. These tests cover the wire layer: the request URL, query
params, error mapping (404 → NotFoundError), and end-to-end
fetch_by_isbn → Publication flow with a mocked upstream.

Catches the failure modes the mapper tests cannot:
  - URL typos (catalogue.bnf.fr vs catalog.bnf.fr, /isbn/ vs /books/)
  - Wrong query-param shape (`q=isbn:X` vs `q=ISBN:X`)
  - Missing 404 → NotFoundError mapping
  - Forgotten/incorrect filter syntax for OpenAlex book lookup
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from quelle.repositories.errors import NetworkError, NotFoundError
from quelle.repositories.sources import bnf, google_books, open_library, openalex
from quelle.settings import Settings


@pytest.fixture
def client() -> httpx.Client:
    """A plain httpx.Client — pytest-httpx intercepts its requests."""
    return httpx.Client()


# --- Open Library --------------------------------------------------------


def test_open_library_fetch_by_isbn_hits_the_isbn_endpoint(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    httpx_mock.add_response(
        url="https://openlibrary.org/isbn/9780140186338.json",
        json={
            "key": "/books/OL1M",
            "title": "The Solid Mandala",
            "publish_date": "1969",
            "publishers": ["Penguin"],
            "isbn_10": ["0140186336"],
            "isbn_13": ["9780140186338"],
            "number_of_pages": 315,
            "subjects": ["Fiction"],
            "authors": [{"key": "/authors/OL1A"}],
            "works": [{"key": "/works/OL1W"}],
        },
    )
    httpx_mock.add_response(
        url="https://openlibrary.org/authors/OL1A.json",
        json={"name": "Patrick White"},
    )
    httpx_mock.add_response(
        url="https://openlibrary.org/works/OL1W.json",
        json={"description": "A novel about twin brothers."},
    )

    publication = open_library.fetch_by_isbn(client, tmp_settings, "9780140186338")

    assert publication.title == "The Solid Mandala"
    assert publication.isbn_13 == "9780140186338"
    assert publication.isbn_10 == "0140186336"
    assert publication.year == 1969
    assert publication.publisher == "Penguin"
    assert publication.page_count == 315
    assert publication.authors[0].name == "Patrick White"
    assert publication.abstract == "A novel about twin brothers."
    assert publication.kind == "book"
    assert publication.resolved_from_chain == ["open_library"]


def test_open_library_fetch_by_isbn_maps_404_to_not_found(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    httpx_mock.add_response(
        url="https://openlibrary.org/isbn/9999999999999.json",
        status_code=404,
        text="not found",
    )
    with pytest.raises(NotFoundError, match="9999999999999"):
        open_library.fetch_by_isbn(client, tmp_settings, "9999999999999")


def test_open_library_swallows_failed_secondary_fetches(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    """Author and work lookups are best-effort — a 500 there must not bubble up."""
    httpx_mock.add_response(
        url="https://openlibrary.org/isbn/9780140186338.json",
        json={
            "key": "/books/OL1M",
            "title": "Edition only",
            "isbn_13": ["9780140186338"],
            "authors": [{"key": "/authors/BAD"}],
            "works": [{"key": "/works/BAD"}],
        },
    )
    httpx_mock.add_response(
        url="https://openlibrary.org/authors/BAD.json",
        status_code=500,
        text="boom",
    )
    httpx_mock.add_response(
        url="https://openlibrary.org/works/BAD.json",
        status_code=500,
        text="boom",
    )

    publication = open_library.fetch_by_isbn(client, tmp_settings, "9780140186338")
    assert publication.title == "Edition only"
    assert publication.authors == []
    assert publication.abstract is None


# --- Google Books --------------------------------------------------------


def test_google_books_fetch_by_isbn_uses_isbn_q_param(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    httpx_mock.add_response(
        url="https://www.googleapis.com/books/v1/volumes?q=isbn%3A9780140186338&maxResults=1",
        json={
            "items": [
                {
                    "volumeInfo": {
                        "title": "Pride and Prejudice",
                        "authors": ["Jane Austen"],
                        "publishedDate": "1813",
                        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780140186338"}],
                    },
                    "accessInfo": {
                        "accessViewStatus": "FULL_PUBLIC_DOMAIN",
                        "pdf": {"isAvailable": True, "downloadLink": "https://x/p.pdf"},
                    },
                }
            ]
        },
    )

    publication = google_books.fetch_by_isbn(client, tmp_settings, "9780140186338")
    assert publication.title == "Pride and Prejudice"
    assert publication.is_open_access is True
    assert publication.pdf_url == "https://x/p.pdf"


def test_google_books_fetch_by_isbn_no_items_raises_not_found(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    httpx_mock.add_response(
        url="https://www.googleapis.com/books/v1/volumes?q=isbn%3A9999999999999&maxResults=1",
        json={"totalItems": 0},
    )
    with pytest.raises(NotFoundError, match="9999999999999"):
        google_books.fetch_by_isbn(client, tmp_settings, "9999999999999")


def test_google_books_passes_api_key_when_configured(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    """Confirms the `key=` query param is sent when GOOGLE_BOOKS_API_KEY is set."""
    from dataclasses import replace

    settings = replace(tmp_settings, google_books_api_key="abc123")
    httpx_mock.add_response(
        url="https://www.googleapis.com/books/v1/volumes?q=isbn%3A9780140186338&maxResults=1&key=abc123",
        json={"items": [{"volumeInfo": {"title": "X"}}]},
    )
    publication = google_books.fetch_by_isbn(client, settings, "9780140186338")
    assert publication.title == "X"


# --- OpenAlex book lookup ------------------------------------------------


def test_openalex_fetch_by_isbn_uses_book_type_filter(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    """Verifies the URL carries `filter=type:book|book-chapter` and `search=<isbn>`."""
    httpx_mock.add_response(
        url=(
            "https://api.openalex.org/works"
            "?search=9780262035613"
            "&filter=type%3Abook%7Cbook-chapter"
            "&per-page=1"
            "&mailto=tests%40example.com"
        ),
        json={
            "results": [
                {
                    "title": "Deep Learning",
                    "type": "book",
                    "publication_year": 2016,
                    "primary_location": {"source": {"display_name": "MIT Press"}},
                }
            ]
        },
    )

    publication = openalex.fetch_by_isbn(client, tmp_settings, "9780262035613")
    assert publication.title == "Deep Learning"
    assert publication.kind == "book"


def test_openalex_fetch_by_isbn_empty_results_raises_not_found(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    httpx_mock.add_response(
        url=(
            "https://api.openalex.org/works"
            "?search=9999999999999"
            "&filter=type%3Abook%7Cbook-chapter"
            "&per-page=1"
            "&mailto=tests%40example.com"
        ),
        json={"results": []},
    )
    with pytest.raises(NotFoundError, match="9999999999999"):
        openalex.fetch_by_isbn(client, tmp_settings, "9999999999999")


# --- BnF -----------------------------------------------------------------


_BNF_RECORD_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
  <srw:numberOfRecords>1</srw:numberOfRecords>
  <srw:records>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>{title}</dc:title>
          <dc:creator>{creator}</dc:creator>
          <dc:publisher>Gallimard</dc:publisher>
          <dc:date>1975</dc:date>
          <dc:identifier>ISBN {isbn}</dc:identifier>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>
"""


def test_bnf_fetch_by_isbn_uses_fuzzy_index_and_validates_match(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    """Confirms the `bib.fuzzyIsbn` CQL field is used (not `bib.isbn`).

    Verified live: BnF's `bib.isbn` only matches ISBN-10 stored in the
    catalogue, while `bib.fuzzyIsbn` accepts any input form. Using the
    wrong field silently returns 0 records for ISBN-13 queries. The
    ISBN used here (Yourcenar's `Archives du Nord`) is one of the
    real ISBNs the live verification confirmed BnF returns correctly.
    """
    httpx_mock.add_response(
        url=(
            "https://catalogue.bnf.fr/api/SRU"
            "?version=1.2"
            "&operation=searchRetrieve"
            "&query=bib.fuzzyIsbn+adj+%229782070373284%22"
            "&recordSchema=dublincore"
            "&maximumRecords=1"
        ),
        text=_BNF_RECORD_TEMPLATE.format(
            title="Archives du Nord",
            creator="Yourcenar, Marguerite",
            isbn="2070373282",  # the matching ISBN-10 form
        ),
    )

    publication = bnf.fetch_by_isbn(client, tmp_settings, "9782070373284")
    assert publication.title == "Archives du Nord"
    assert publication.isbn_10 == "2070373282"
    assert publication.kind == "book"


def test_bnf_post_validation_rejects_fuzzy_false_positive(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    """Verified live: `bib.fuzzyIsbn` returns numerically-close hits for
    ISBNs not in the catalogue. Post-validation must reject those so
    the resolver chain falls through to the next source.
    """
    httpx_mock.add_response(
        url=(
            "https://catalogue.bnf.fr/api/SRU"
            "?version=1.2"
            "&operation=searchRetrieve"
            "&query=bib.fuzzyIsbn+adj+%229782070407132%22"
            "&recordSchema=dublincore"
            "&maximumRecords=1"
        ),
        text=_BNF_RECORD_TEMPLATE.format(
            title="Histoire des religions",
            creator="Puech, Henri-Charles",
            isbn="2070407098",  # different from the queried ISBN's pair
        ),
    )

    with pytest.raises(NotFoundError, match="different ISBN"):
        bnf.fetch_by_isbn(client, tmp_settings, "9782070407132")


def test_bnf_fetch_by_isbn_zero_records_raises_not_found(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    httpx_mock.add_response(
        url=(
            "https://catalogue.bnf.fr/api/SRU"
            "?version=1.2"
            "&operation=searchRetrieve"
            "&query=bib.fuzzyIsbn+adj+%229999999999999%22"
            "&recordSchema=dublincore"
            "&maximumRecords=1"
        ),
        text=(
            '<?xml version="1.0"?>'
            '<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">'
            "<srw:numberOfRecords>0</srw:numberOfRecords></srw:searchRetrieveResponse>"
        ),
    )
    with pytest.raises(NotFoundError):
        bnf.fetch_by_isbn(client, tmp_settings, "9999999999999")


def test_bnf_500_response_raises_network_error(
    httpx_mock: HTTPXMock, client: httpx.Client, tmp_settings: Settings
) -> None:
    httpx_mock.add_response(
        url=(
            "https://catalogue.bnf.fr/api/SRU"
            "?version=1.2"
            "&operation=searchRetrieve"
            "&query=bib.fuzzyIsbn+adj+%229780140186338%22"
            "&recordSchema=dublincore"
            "&maximumRecords=1"
        ),
        status_code=500,
        text="boom",
    )
    with pytest.raises(NetworkError):
        bnf.fetch_by_isbn(client, tmp_settings, "9780140186338")
