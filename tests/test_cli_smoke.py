"""Smoke tests for the CLI — wire-up only, no real network."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


def test_version_command_json() -> None:
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    assert '"publication-manager"' in result.output


def test_version_command_plain() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "publication-manager 0.1.0" in result.output


def test_config_show_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLICATIONS_HOME", str(tmp_path))
    monkeypatch.setenv("PUBLICATIONS_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["config", "--json"])
    assert result.exit_code == 0
    assert "state_dir" in result.output
    assert "alice@example.com" in result.output
    # The state dir should have been created by ensure_dirs.
    assert (tmp_path / ".publications-state" / "pdfs").is_dir()


def test_fetch_requires_query() -> None:
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code != 0
