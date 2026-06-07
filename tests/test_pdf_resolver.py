"""Tests for the PDF fallback chain and local-PDF resolution."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from quelle.models.publication import Publication
from quelle.repositories.errors import UserError
from quelle.services.citekey import base_key
from quelle.services.pdf_resolver import resolve_and_download, resolve_local_pdf


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


# --- resolve_local_pdf ----------------------------------------------------


def test_resolve_local_pdf_reads_embedded_metadata(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Title (Deep Learning Survey) "
        b"/CreationDate (D:20190512093000)>>endobj\n%%EOF"
    )
    pub = resolve_local_pdf(pdf)
    assert pub.title == "Deep Learning Survey"
    assert pub.year == 2019
    assert pub.kind is None  # maps to the knoten `document` vault kind
    assert pub.local_pdf_path == str(pdf)
    assert base_key(pub) == "DeepLearningSurvey2019"


def test_resolve_local_pdf_degrades_to_filename_and_mtime(tmp_path: Path) -> None:
    pdf = tmp_path / "my-thesis-draft.pdf"
    pdf.write_bytes(b"%PDF-1.4\nno info dict here\n%%EOF")
    os.utime(pdf, (1577880000, 1577880000))  # 2020-01-01
    pub = resolve_local_pdf(pdf)
    assert pub.title == "my-thesis-draft"
    assert pub.year == 2020
    assert base_key(pub) == "MyThesisDraft2020"


def test_resolve_local_pdf_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(UserError):
        resolve_local_pdf(tmp_path / "nope.pdf")
