"""Smoke tests for the CLI — wire-up only, no real network."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from quelle.cli.main import app

runner = CliRunner()


def test_version_command_json() -> None:
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    assert '"quelle"' in result.output


def test_version_command_plain() -> None:
    from quelle import __version__

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"quelle {__version__}" in result.output


def test_config_show_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0
    assert "cache_dir" in result.output
    assert "alice@example.com" in result.output
    # ensure_dirs should have created data/pdfs and cache dirs.
    assert (tmp_path / "data" / "pdfs").is_dir()
    assert (tmp_path / "cache").is_dir()


def test_fetch_requires_query() -> None:
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code != 0


def test_search_requires_query() -> None:
    result = runner.invoke(app, ["search"])
    assert result.exit_code != 0


def test_search_renders_json_with_mocked_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub the search service and assert the CLI shape — no network."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.models.publication import Author
    from quelle.models.search import MergedHit

    fake = MergedHit(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani")],
        year=2017,
        type="article",
        doi="10.48550/arxiv.1706.03762",
        arxiv_id="1706.03762",
        sources=["openalex", "arxiv"],
        source_ids={"openalex": "https://openalex.org/W7"},
        score=0.0488,
    )
    monkeypatch.setattr(cli_main.search_service, "search", lambda *args, **kwargs: [fake])

    result = runner.invoke(app, ["search", "attention", "--json"])
    assert result.exit_code == 0
    assert "Attention Is All You Need" in result.output
    assert '"id": "doi:10.48550/arxiv.1706.03762"' in result.output
    assert '"id_resolvable": true' in result.output


def test_search_rejects_invalid_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["search", "x", "--type", "movies"])
    assert result.exit_code == 1
