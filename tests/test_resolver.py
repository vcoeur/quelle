"""Unit tests for the resolver's query-shape routing.

The resolver itself is tested end-to-end via pytest-httpx in the CLI
smoke tests. Here we cover only the private `_extract_doi` helper
since that's the piece most likely to regress, plus the explicit
Google-Scholar-URL rejection path.
"""

from __future__ import annotations

import httpx
import pytest

from quelle.repositories.errors import UserError
from quelle.services.resolver import _extract_doi, resolve
from quelle.settings import Settings


def test_extract_doi_bare() -> None:
    assert _extract_doi("10.1234/abcd") == "10.1234/abcd"


def test_extract_doi_url() -> None:
    assert _extract_doi("https://doi.org/10.1234/abcd") == "10.1234/abcd"


def test_extract_doi_prefix() -> None:
    assert _extract_doi("doi:10.1234/abcd") == "10.1234/abcd"


def test_extract_doi_lowercased() -> None:
    assert _extract_doi("10.1234/ABCD") == "10.1234/abcd"


def test_extract_doi_rejects_non_doi() -> None:
    assert _extract_doi("attention is all you need") is None


def test_extract_doi_rejects_arxiv_id() -> None:
    assert _extract_doi("1706.03762") is None


@pytest.mark.parametrize(
    "scholar_url",
    [
        "https://scholar.google.com/scholar?cluster=1234",
        "http://scholar.google.co.uk/citations?user=abc",
        "https://scholar.google.de/scholar?q=attention",
    ],
)
def test_resolve_rejects_google_scholar_urls(scholar_url: str, tmp_settings: Settings) -> None:
    """Scholar has no API; pasting a Scholar URL must surface a clean UserError."""
    client = httpx.Client()
    try:
        with pytest.raises(UserError, match="Google Scholar"):
            resolve(client, tmp_settings, scholar_url)
    finally:
        client.close()
