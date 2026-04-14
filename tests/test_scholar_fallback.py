"""Tests for the Scholar URL fallback and its routing."""

from __future__ import annotations

import sys
import types

import pytest

from app.repositories.errors import ConfigError, NotFoundError
from app.repositories.sources.scholar_fallback import extract_title_from_scholar_url
from app.services.resolver import _SCHOLAR_HOST_RE


def _install_fake_scholarly(monkeypatch: pytest.MonkeyPatch, *, result=None, raises=None) -> None:
    """Inject a fake `scholarly` module so the helper runs without the real package."""
    fake_module = types.ModuleType("scholarly")

    class _FakeScholarly:
        def search_single_pub(self, query):  # noqa: ARG002 — fake signature
            if raises is not None:
                raise raises
            return result

    fake_module.scholarly = _FakeScholarly()
    monkeypatch.setitem(sys.modules, "scholarly", fake_module)


def test_scholar_host_regex_matches_common_forms() -> None:
    assert _SCHOLAR_HOST_RE.search("https://scholar.google.com/scholar?cluster=1234")
    assert _SCHOLAR_HOST_RE.search("http://scholar.google.co.uk/citations")
    assert not _SCHOLAR_HOST_RE.search("https://example.com/paper")


def test_extract_title_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_scholarly(
        monkeypatch,
        result={"bib": {"title": "Attention Is All You Need"}},
    )
    title = extract_title_from_scholar_url("https://scholar.google.com/scholar?cluster=1234")
    assert title == "Attention Is All You Need"


def test_extract_title_raises_not_found_when_title_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_scholarly(monkeypatch, result={"bib": {}})
    with pytest.raises(NotFoundError):
        extract_title_from_scholar_url("https://scholar.google.com/scholar?cluster=1234")


def test_extract_title_wraps_scholarly_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_scholarly(monkeypatch, raises=RuntimeError("captcha"))
    with pytest.raises(NotFoundError, match="captcha"):
        extract_title_from_scholar_url("https://scholar.google.com/scholar?cluster=1234")


def test_extract_title_raises_config_error_when_scholarly_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ensure any cached `scholarly` module is gone and importing it fails.
    monkeypatch.delitem(sys.modules, "scholarly", raising=False)

    def _fail_import(*args, **kwargs):  # noqa: ARG001
        raise ImportError("no module named 'scholarly'")

    monkeypatch.setattr("builtins.__import__", _fail_import)
    with pytest.raises(ConfigError, match="scholarly"):
        extract_title_from_scholar_url("https://scholar.google.com/scholar?cluster=1234")
