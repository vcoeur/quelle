"""Tests for `quelle skill install` / `status` to a temp --dest."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from quelle.cli.main import app
from quelle.cli.skill import _bundled_skill_text

runner = CliRunner()


def test_skill_install_to_dest(tmp_path: Path) -> None:
    dest = tmp_path / "skills" / "quelle"
    result = runner.invoke(app, ["skill", "install", "--dest", str(dest), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    target = Path(payload["installed"])
    assert target == dest / "SKILL.md"
    assert target.read_text(encoding="utf-8") == _bundled_skill_text()
    assert payload["overwritten"] is False


def test_skill_install_refuses_overwrite_without_force(tmp_path: Path) -> None:
    dest = tmp_path / "quelle"
    runner.invoke(app, ["skill", "install", "--dest", str(dest)])
    result = runner.invoke(app, ["skill", "install", "--dest", str(dest), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "user"


def test_skill_install_force_overwrites(tmp_path: Path) -> None:
    dest = tmp_path / "quelle"
    runner.invoke(app, ["skill", "install", "--dest", str(dest)])
    result = runner.invoke(app, ["skill", "install", "--dest", str(dest), "--force", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["overwritten"] is True


def test_skill_install_rejects_dest_with_scope(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skill", "install", "--dest", str(tmp_path), "--user", "--json"])
    assert result.exit_code == 1


def test_skill_status_json_reports_targets() -> None:
    result = runner.invoke(app, ["skill", "status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    scopes = {row["scope"] for row in payload["targets"]}
    assert scopes == {"user", "project", "claude"}
    assert payload["bundled_bytes"] > 0
