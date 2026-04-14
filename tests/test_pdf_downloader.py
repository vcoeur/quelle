"""Tests for the streaming PDF downloader."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.repositories.errors import NetworkError
from app.repositories.pdf_downloader import download_pdf


def _make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_writes_pdf_to_dest(tmp_path: Path, tmp_settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"%PDF-1.4 tiny fake body",
            headers={"content-type": "application/pdf"},
        )

    dest = tmp_path / "Vaswani2017.pdf"
    with _make_client(handler) as client:
        result = download_pdf(client, "https://x/y.pdf", dest, tmp_settings)
    assert dest.exists()
    assert dest.read_bytes().startswith(b"%PDF")
    assert result.size_bytes > 0
    assert "application/pdf" in result.content_type


def test_download_rejects_non_pdf_content(tmp_path: Path, tmp_settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>not a pdf</html>", headers={"content-type": "text/html"}
        )

    dest = tmp_path / "bad.pdf"
    with _make_client(handler) as client, pytest.raises(NetworkError, match="not a PDF"):
        download_pdf(client, "https://x/y.html", dest, tmp_settings)
    assert not dest.exists()


def test_download_accepts_pdf_magic_bytes_even_with_wrong_content_type(
    tmp_path: Path, tmp_settings
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"%PDF-1.7 body",
            headers={"content-type": "application/octet-stream"},
        )

    dest = tmp_path / "magic.pdf"
    with _make_client(handler) as client:
        download_pdf(client, "https://x/y.pdf", dest, tmp_settings)
    assert dest.exists()


def test_download_rejects_files_over_max_size(tmp_path: Path, tmp_settings) -> None:
    from dataclasses import replace

    big_settings = replace(tmp_settings, max_pdf_mb=0)  # zero MB -> any body too big
    body = b"%PDF" + b"x" * 1024

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    dest = tmp_path / "big.pdf"
    with _make_client(handler) as client, pytest.raises(NetworkError, match="exceeds"):
        download_pdf(client, "https://x/y.pdf", dest, big_settings)
    assert not dest.exists()


def test_download_raises_on_http_error(tmp_path: Path, tmp_settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"missing")

    dest = tmp_path / "missing.pdf"
    with _make_client(handler) as client, pytest.raises(NetworkError, match="404"):
        download_pdf(client, "https://x/y.pdf", dest, tmp_settings)
