"""Unit tests for the Unpaywall helpers."""

from __future__ import annotations

import pytest

from app.repositories.errors import ConfigError
from app.repositories.sources.unpaywall import extract_pdf_url, lookup_by_doi


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
