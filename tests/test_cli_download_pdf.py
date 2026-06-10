"""CLI tests for the `--download-pdf` branches of `fetch` and `resolve`.

The resolver and the PDF chain are monkeypatched (no network); the cache
is real, so the tests also pin the contract that a successful download is
persisted back to the cached row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quelle.cli.main import app
from quelle.models.publication import Author, Publication
from quelle.repositories.cache import Cache
from quelle.services.pdf_resolver import PdfOutcome

runner = CliRunner()

DOI = "10.48550/arxiv.1706.03762"


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")


def _publication(**overrides) -> Publication:
    base = dict(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani")],
        year=2017,
        doi=DOI,
        kind="article",
    )
    base.update(overrides)
    return Publication(**base)


def _patch_resolvers(
    monkeypatch: pytest.MonkeyPatch,
    publication: Publication,
    outcome: PdfOutcome | None,
) -> dict:
    """Stub both `fetch`'s and `resolve`'s resolver plus the PDF chain.

    `outcome=None` makes the PDF chain fail the test if it is reached —
    for asserting the chain is skipped. Returns a record of the calls.
    """
    from quelle.cli import main as cli_main

    calls: dict = {"download": 0}

    def fake_resolve(client, settings, query, **kwargs):
        return publication

    def fake_download(client, settings, pub, dest_dir):
        calls["download"] += 1
        calls["dest_dir"] = dest_dir
        if outcome is None:
            raise AssertionError("PDF chain must not run for this input")
        return outcome

    monkeypatch.setattr(cli_main, "resolve_with_enrichment", fake_resolve)
    monkeypatch.setattr(cli_main, "resolve_any", fake_resolve)
    # main.py imports this lazily inside the command body, so patch the source.
    monkeypatch.setattr("quelle.services.pdf_resolver.resolve_and_download", fake_download)
    return calls


def test_fetch_download_pdf_success_persists_to_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    pdf_path = tmp_path / "data" / "pdfs" / "Vaswani2017.pdf"
    calls = _patch_resolvers(
        monkeypatch,
        _publication(),
        PdfOutcome(local_path=pdf_path, sources_tried=["openalex"]),
    )

    result = runner.invoke(app, ["--json", "fetch", DOI, "--download-pdf"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["local_pdf_path"] == str(pdf_path)
    assert calls["download"] == 1
    # The chain is pointed at the configured PDF dir.
    assert calls["dest_dir"] == tmp_path / "data" / "pdfs"
    # The downloaded path is persisted back into the cached row.
    with Cache.open(tmp_path / "cache" / "cache.sqlite") as cache:
        row = cache.get_by_doi(DOI)
        assert row is not None
        assert row.local_pdf_path == str(pdf_path)


def test_fetch_download_pdf_failure_leaves_path_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed chain (no OA copy) is not an error — metadata still prints."""
    _env(monkeypatch, tmp_path)
    calls = _patch_resolvers(
        monkeypatch,
        _publication(),
        PdfOutcome(local_path=None, sources_tried=[], reason_if_none="no_oa_copy"),
    )

    result = runner.invoke(app, ["--json", "fetch", DOI, "--download-pdf"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["local_pdf_path"] is None
    assert calls["download"] == 1


def test_fetch_download_pdf_no_cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-cache --download-pdf` downloads without touching the cache."""
    _env(monkeypatch, tmp_path)
    pdf_path = tmp_path / "data" / "pdfs" / "Vaswani2017.pdf"
    calls = _patch_resolvers(
        monkeypatch,
        _publication(),
        PdfOutcome(local_path=pdf_path, sources_tried=["openalex"]),
    )

    result = runner.invoke(app, ["--json", "fetch", DOI, "--download-pdf", "--no-cache"])
    assert result.exit_code == 0
    assert json.loads(result.output)["local_pdf_path"] == str(pdf_path)
    assert calls["download"] == 1
    with Cache.open(tmp_path / "cache" / "cache.sqlite") as cache:
        assert cache.get_by_doi(DOI) is None


def test_resolve_download_pdf_success_persists_to_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    pdf_path = tmp_path / "data" / "pdfs" / "Vaswani2017.pdf"
    calls = _patch_resolvers(
        monkeypatch,
        _publication(),
        PdfOutcome(local_path=pdf_path, sources_tried=["openalex"]),
    )

    result = runner.invoke(app, ["--json", "resolve", DOI, "--download-pdf"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["local_pdf_path"] == str(pdf_path)
    assert payload["x_vcoeur"]["citekey"] == "Vaswani2017"
    assert calls["download"] == 1
    with Cache.open(tmp_path / "cache" / "cache.sqlite") as cache:
        row = cache.get_by_doi(DOI)
        assert row is not None
        assert row.local_pdf_path == str(pdf_path)


def test_resolve_download_pdf_failure_keeps_source_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    calls = _patch_resolvers(
        monkeypatch,
        _publication(),
        PdfOutcome(local_path=None, sources_tried=["openalex"], reason_if_none="openalex failed"),
    )

    result = runner.invoke(app, ["--json", "resolve", DOI, "--download-pdf", "--no-cache"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["local_pdf_path"] is None
    assert payload["x_vcoeur"]["citekey"] == "Vaswani2017"
    assert calls["download"] == 1


def test_resolve_download_pdf_skipped_when_already_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A publication that already carries a local PDF skips the chain."""
    _env(monkeypatch, tmp_path)
    existing = tmp_path / "already" / "Vaswani2017.pdf"
    calls = _patch_resolvers(
        monkeypatch,
        _publication(local_pdf_path=str(existing)),
        outcome=None,  # the fake download raises if reached
    )

    result = runner.invoke(app, ["--json", "resolve", DOI, "--download-pdf", "--no-cache"])
    assert result.exit_code == 0
    assert json.loads(result.output)["local_pdf_path"] == str(existing)
    assert calls["download"] == 0


def test_resolve_unreadable_taken_file_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing --taken-file is a user error (exit 1), not a traceback."""
    _env(monkeypatch, tmp_path)
    result = runner.invoke(
        app,
        ["resolve", "x", "--taken-file", str(tmp_path / "does-not-exist.txt"), "--no-cache"],
    )
    assert result.exit_code == 1
    assert "could not read taken-set" in result.output
