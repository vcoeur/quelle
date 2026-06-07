"""CLI tests for `quelle resolve` — routing, taken-set, x_vcoeur, --csl.

`resolve_any` is monkeypatched so no network runs; the assertions cover
the CLI wiring: Source shape, CiteKey minting, and CSL export.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quelle.cli.main import app
from quelle.models.publication import Author, Publication

runner = CliRunner()


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, pub: Publication) -> dict:
    from quelle.cli import main as cli_main

    captured: dict = {}

    def fake_resolve_any(client, settings, raw_input, **kwargs):
        captured["input"] = raw_input
        captured["kwargs"] = kwargs
        return pub

    monkeypatch.setattr(cli_main, "resolve_any", fake_resolve_any)
    return captured


def test_resolve_emits_source_with_x_vcoeur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    pub = Publication(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani")],
        year=2017,
        kind="article",
    )
    _patch_resolve(monkeypatch, pub)
    result = runner.invoke(app, ["--json", "resolve", "attention", "--no-cache"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["citation_key"] == "Vaswani2017"
    assert payload["x_vcoeur"] == {
        "citekey": "Vaswani2017",
        "vault_id": None,
        "vault_kind": "article",
        "confidence": None,
    }


def test_resolve_web_source_vault_kind_web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    pub = Publication(
        title="X1", venue="Bambu Lab", year=2024, source_url="https://bambulab.com/x1", kind="web"
    )
    captured = _patch_resolve(monkeypatch, pub)
    result = runner.invoke(app, ["--json", "resolve", "https://bambulab.com/x1", "--no-cache"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["x_vcoeur"]["vault_kind"] == "web"
    assert payload["x_vcoeur"]["citekey"] == "BambuLab2024-x1"
    assert captured["input"] == "https://bambulab.com/x1"


def test_resolve_mints_against_inline_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    pub = Publication(title="X", authors=[Author(name="Alice")], year=2026)
    _patch_resolve(monkeypatch, pub)
    result = runner.invoke(
        app, ["--json", "resolve", "x", "--taken", "Alice2026,Alice2026a", "--no-cache"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    # Base key is taken twice over → minted to the next free suffix.
    assert payload["citation_key"] == "Alice2026"
    assert payload["x_vcoeur"]["citekey"] == "Alice2026b"


def test_resolve_mints_against_knoten_json_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    pub = Publication(title="X", authors=[Author(name="Alice")], year=2026)
    _patch_resolve(monkeypatch, pub)
    result = runner.invoke(
        app,
        ["--json", "resolve", "x", "--taken-file", "-", "--no-cache"],
        input='{"citekeys": ["Alice2026"]}',
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["x_vcoeur"]["citekey"] == "Alice2026a"


def test_resolve_csl_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    pub = Publication(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani")],
        year=2017,
        doi="10.48550/arxiv.1706.03762",
        kind="article",
    )
    _patch_resolve(monkeypatch, pub)
    result = runner.invoke(app, ["--json", "resolve", "attention", "--csl", "--no-cache"])
    assert result.exit_code == 0
    csl = json.loads(result.output)
    assert csl["id"] == "Vaswani2017"
    assert csl["type"] == "article-journal"
    assert csl["author"] == [{"family": "Vaswani", "given": "Ashish"}]
    assert csl["issued"] == {"date-parts": [[2017]]}
    assert "x_vcoeur" not in csl  # CSL is an export, not the Source


def test_resolve_rejects_both_book_and_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["resolve", "x", "--book", "--article", "--no-cache"])
    assert result.exit_code == 1
