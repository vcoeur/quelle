"""Unit tests for the Unpaywall helpers."""

from __future__ import annotations

import httpx
import pytest

from quelle.repositories.errors import ConfigError, RateLimitError
from quelle.repositories.sources import unpaywall
from quelle.repositories.sources.unpaywall import extract_pdf_url, lookup_by_doi


def test_extract_pdf_url_from_best_oa_location() -> None:
    payload = {
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/paper.pdf",
            "license": "cc-by",
        },
    }
    assert extract_pdf_url(payload) == "https://example.com/paper.pdf"


def test_extract_pdf_url_none_when_no_oa() -> None:
    payload = {"is_oa": False, "best_oa_location": None}
    assert extract_pdf_url(payload) is None


def test_extract_pdf_url_missing_field() -> None:
    payload = {"best_oa_location": {"license": "cc-by"}}
    assert extract_pdf_url(payload) is None


def test_lookup_by_doi_raises_without_email(tmp_settings) -> None:
    from dataclasses import replace

    blank = replace(tmp_settings, contact_email="", unpaywall_email="")
    with pytest.raises(ConfigError, match="Unpaywall requires an email"):
        lookup_by_doi(client=None, settings=blank, doi="10.x/y")


def test_lookup_by_doi_404_returns_empty_dict(httpx_mock, tmp_settings) -> None:
    """No Unpaywall record for the DOI is a normal miss, not an error."""
    unpaywall._reset_rate_limit_for_tests()
    httpx_mock.add_response(status_code=404, text="not found")
    with httpx.Client() as client:
        assert lookup_by_doi(client, tmp_settings, "10.1/missing") == {}


def test_lookup_by_doi_propagates_rate_limit(httpx_mock, tmp_settings) -> None:
    """429 must not be swallowed into 'no OA copy' — callers need the signal."""
    unpaywall._reset_rate_limit_for_tests()
    httpx_mock.add_response(status_code=429, text="slow down")
    with httpx.Client() as client, pytest.raises(RateLimitError):
        lookup_by_doi(client, tmp_settings, "10.1/limited")


def test_lookup_by_doi_other_network_error_warns_and_degrades(
    httpx_mock, tmp_settings, capsys
) -> None:
    """A 5xx degrades to {} (enrichment is best-effort) with a stderr warning."""
    unpaywall._reset_rate_limit_for_tests()
    httpx_mock.add_response(status_code=503, text="boom")
    with httpx.Client() as client:
        assert lookup_by_doi(client, tmp_settings, "10.1/flaky") == {}
    captured = capsys.readouterr()
    assert "Unpaywall lookup failed" in captured.err
    assert "10.1/flaky" in captured.err


def test_lookup_by_doi_programming_errors_propagate(
    tmp_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-network exceptions are bugs and must not be masked as 'no record'."""
    unpaywall._reset_rate_limit_for_tests()

    def raise_type_error(*args, **kwargs):
        raise TypeError("bug")

    monkeypatch.setattr(unpaywall, "get_json", raise_type_error)
    with pytest.raises(TypeError, match="bug"):
        lookup_by_doi(client=None, settings=tmp_settings, doi="10.1/x")


def test_lookup_by_doi_percent_encodes_doi(httpx_mock, tmp_settings) -> None:
    """`?` / `#` in a DOI must not truncate the URL path or inject params."""
    unpaywall._reset_rate_limit_for_tests()
    httpx_mock.add_response(
        url="https://api.unpaywall.org/v2/10.1000/weird%3Fa%23b?email=tests%40example.com",
        json={"best_oa_location": None},
    )
    with httpx.Client() as client:
        assert lookup_by_doi(client, tmp_settings, "10.1000/weird?a#b") == {
            "best_oa_location": None
        }
