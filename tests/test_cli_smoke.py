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
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "quelle 0.1.0" in result.output


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
