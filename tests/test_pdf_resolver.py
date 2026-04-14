"""Tests for the PDF fallback chain."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.models.publication import Publication
from app.services.pdf_resolver import resolve_and_download


def _publication_with_arxiv() -> Publication:
    return Publication(
        title="Attention Is All You Need",
        year=2017,
        arxiv_id="1706.03762",
        pdf_url="https://example.com/direct.pdf",
        doi="10.48550/arxiv.1706.03762",
        resolved_from_chain=["openalex"],
    )


def _publication_no_pdf() -> Publication:
    return Publication(
        title="Obscure Old Paper",
        year=1958,
        resolved_from_chain=["openalex"],
    )


def test_chain_stops_at_first_success(tmp_path: Path, tmp_settings) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            content=b"%PDF-1.4 direct",
            headers={"content-type": "application/pdf"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = resolve_and_download(client, tmp_settings, _publication_with_arxiv(), tmp_path)

    assert outcome.local_path is not None
    assert outcome.local_path.read_bytes().startswith(b"%PDF")
    # Only the first candidate should have been hit.
    assert len(calls) == 1
    assert calls[0] == "https://example.com/direct.pdf"
    assert outcome.sources_tried == ["openalex"]


def test_chain_falls_through_to_arxiv(tmp_path: Path, tmp_settings) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "example.com" in str(request.url):
            return httpx.Response(500, content=b"oops")
        return httpx.Response(
            200,
            content=b"%PDF-1.4 arxiv body",
            headers={"content-type": "application/pdf"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = resolve_and_download(client, tmp_settings, _publication_with_arxiv(), tmp_path)

    assert outcome.local_path is not None
    assert outcome.sources_tried == ["openalex", "arxiv"]
    assert any("arxiv.org/pdf/1706.03762" in url for url in calls)


def test_no_candidates_returns_no_oa_copy(tmp_path: Path, tmp_settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network call should happen for a paper with no URLs")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = resolve_and_download(client, tmp_settings, _publication_no_pdf(), tmp_path)

    assert outcome.local_path is None
    assert outcome.reason_if_none == "no_oa_copy"
    assert outcome.sources_tried == []


def test_total_failure_preserves_last_reason(tmp_path: Path, tmp_settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    publication = Publication(
        title="x",
        pdf_url="https://a/1.pdf",
        arxiv_id="1706.03762",
        resolved_from_chain=["openalex"],
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = resolve_and_download(client, tmp_settings, publication, tmp_path)

    assert outcome.local_path is None
    assert "failed" in (outcome.reason_if_none or "")
