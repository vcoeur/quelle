"""Tests for `quelle skill install` / `status` to a temp --dest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quelle.cli import skill as skill_module
from quelle.cli.main import app
from quelle.cli.skill import _bundled_skill_text

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_skill_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep skill-dir resolution off the developer's real $HOME and cwd.

    `USER_SKILL_DIR` / `CLAUDE_SKILL_DIR` are computed from `Path.home()`
    at import time, so patching $HOME afterwards would not move them —
    patch the module globals instead, and chdir into tmp_path so the
    project scope (`Path.cwd()`-relative) is isolated too.
    """
    monkeypatch.setattr(
        skill_module, "USER_SKILL_DIR", tmp_path / "home" / ".config" / "agents-skills" / "quelle"
    )
    monkeypatch.setattr(
        skill_module, "CLAUDE_SKILL_DIR", tmp_path / "home" / ".claude-skills" / "quelle"
    )
    monkeypatch.chdir(tmp_path)


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


def test_skill_status_honours_root_json_flag() -> None:
    """`quelle --json skill status` must emit JSON like every other command."""
    result = runner.invoke(app, ["--json", "skill", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert {row["scope"] for row in payload["targets"]} == {"user", "project", "claude"}


def test_skill_install_honours_root_json_flag(tmp_path: Path) -> None:
    dest = tmp_path / "skills" / "quelle"
    result = runner.invoke(app, ["--json", "skill", "install", "--dest", str(dest)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert Path(payload["installed"]) == dest / "SKILL.md"


def test_skill_install_user_scope_lands_in_user_dir() -> None:
    """`--user` (also the default scope) targets the user skills dir."""
    result = runner.invoke(app, ["skill", "install", "--user", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert Path(payload["installed"]) == skill_module.USER_SKILL_DIR / "SKILL.md"


def test_skill_install_project_scope_lands_under_cwd(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skill", "install", "--project", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert Path(payload["installed"]) == tmp_path / ".agents" / "skills" / "quelle" / "SKILL.md"


def test_skill_install_claude_scope_lands_in_claude_dir() -> None:
    result = runner.invoke(app, ["skill", "install", "--claude", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert Path(payload["installed"]) == skill_module.CLAUDE_SKILL_DIR / "SKILL.md"


def test_skill_install_rejects_two_scopes() -> None:
    result = runner.invoke(app, ["skill", "install", "--user", "--claude", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "user"


def test_skill_status_text_reports_install_states() -> None:
    """The plain-text status walks all three scopes with their states."""
    install = runner.invoke(app, ["skill", "install", "--user"])
    assert install.exit_code == 0
    result = runner.invoke(app, ["skill", "status"])
    assert result.exit_code == 0
    assert "up to date" in result.output
    assert "not installed" in result.output


def test_skill_status_flags_stale_copy() -> None:
    """An installed SKILL.md that drifted from the bundled copy reads STALE."""
    runner.invoke(app, ["skill", "install", "--user"])
    target = skill_module.USER_SKILL_DIR / "SKILL.md"
    target.write_text("drifted", encoding="utf-8")
    result = runner.invoke(app, ["skill", "status"])
    assert result.exit_code == 0
    assert "STALE" in result.output
