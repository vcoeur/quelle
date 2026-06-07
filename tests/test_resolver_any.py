"""Routing tests for `resolve_any` — each input class hits the right path.

All sub-resolvers are monkeypatched, so no network or real parsing runs;
the assertions are purely about which branch `resolve_any` selected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quelle.models.publication import Publication
from quelle.services import pdf_resolver, resolver, url_resolver


def _stub(monkeypatch: pytest.MonkeyPatch, calls: dict) -> None:
    """Replace every downstream resolver with a recorder."""

    def fake_local_pdf(path: Path) -> Publication:
        calls["pdf"] = str(path)
        return Publication(title="pdf")

    def fake_url(client, settings, url: str) -> Publication:
        calls["url"] = url
        return Publication(title="web", kind="web")

    def fake_enrich(client, settings, query, **kwargs) -> Publication:
        calls["enrich"] = query
        calls["kwargs"] = kwargs
        return Publication(title="rich")

    monkeypatch.setattr(pdf_resolver, "resolve_local_pdf", fake_local_pdf)
    monkeypatch.setattr(url_resolver, "resolve_url", fake_url)
    monkeypatch.setattr(resolver, "resolve_with_enrichment", fake_enrich)


def test_routes_local_pdf(tmp_path: Path, tmp_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}
    _stub(monkeypatch, calls)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    pub = resolver.resolve_any(None, tmp_settings, str(pdf))
    assert pub.title == "pdf"
    assert calls["pdf"] == str(pdf)


def test_routes_plain_url_to_url_resolver(tmp_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}
    _stub(monkeypatch, calls)
    pub = resolver.resolve_any(None, tmp_settings, "https://bambulab.com/en/x1")
    assert pub.kind == "web"
    assert calls["url"] == "https://bambulab.com/en/x1"
    assert "enrich" not in calls


def test_routes_doi_landing_url_to_rich_resolver(
    tmp_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict = {}
    _stub(monkeypatch, calls)
    pub = resolver.resolve_any(None, tmp_settings, "https://doi.org/10.1109/83.902291")
    assert pub.title == "rich"
    assert calls["enrich"] == "10.1109/83.902291"
    assert "url" not in calls


def test_routes_arxiv_url_to_rich_resolver(tmp_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}
    _stub(monkeypatch, calls)
    resolver.resolve_any(None, tmp_settings, "https://arxiv.org/abs/1706.03762")
    assert calls["enrich"] == "1706.03762"


def test_routes_explicit_id_and_free_text_to_enrichment(
    tmp_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict = {}
    _stub(monkeypatch, calls)
    resolver.resolve_any(None, tmp_settings, "10.1109/83.902291")
    assert calls["enrich"] == "10.1109/83.902291"
    resolver.resolve_any(
        None, tmp_settings, "attention is all you need", type_hint="article", author="vaswani"
    )
    assert calls["enrich"] == "attention is all you need"
    assert calls["kwargs"]["type_hint"] == "article"
    assert calls["kwargs"]["author"] == "vaswani"
