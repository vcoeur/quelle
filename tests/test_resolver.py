"""Unit tests for the resolver's query-shape routing.

The resolver itself is tested end-to-end via pytest-httpx in the CLI
smoke tests. Here we cover only the private `_extract_doi` helper
since that's the piece most likely to regress.
"""

from __future__ import annotations

from app.services.resolver import _extract_doi


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
