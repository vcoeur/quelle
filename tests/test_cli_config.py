"""Tests for the `quelle config` sub-app and the `quelle init` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quelle.cli.main import app

runner = CliRunner()


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Route config/data/cache to disposable tmp dirs and clear HOME-side state."""
    config = tmp_path / "cfg"
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(config))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(data))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(cache))
    monkeypatch.delenv("PUBLICATIONS_HOME", raising=False)
    # Isolate HOME so the legacy migration never touches the real user's dirs.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    return config, data, cache


def test_init_creates_dirs_and_seeds_env(isolated_env: tuple[Path, Path, Path]) -> None:
    config, data, cache = isolated_env
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert config.is_dir()
    assert (data / "pdfs").is_dir()
    assert cache.is_dir()
    env_file = config / ".env"
    assert env_file.exists()
    assert "QUELLE_CONTACT_EMAIL" in env_file.read_text()
    assert "created" in result.output.lower()


def test_init_is_idempotent(isolated_env: tuple[Path, Path, Path]) -> None:
    config, _data, _cache = isolated_env
    env_file = config / ".env"

    runner.invoke(app, ["init"])
    env_file.write_text("QUELLE_CONTACT_EMAIL=first@example.com\n")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    # Second run must not overwrite the user's edits.
    assert env_file.read_text() == "QUELLE_CONTACT_EMAIL=first@example.com\n"
    assert "already present" in result.output.lower()


def test_config_path_plain(isolated_env: tuple[Path, Path, Path]) -> None:
    config, data, cache = isolated_env
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    out = result.output
    assert f"config_dir: {config}" in out
    assert f"data_dir: {data}" in out
    assert f"cache_dir: {cache}" in out
    assert "mode:" in out
    assert "env_file:" in out


def test_config_path_json(isolated_env: tuple[Path, Path, Path]) -> None:
    config, data, cache = isolated_env
    result = runner.invoke(app, ["config", "path", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["config_dir"] == str(config)
    assert payload["data_dir"] == str(data)
    assert payload["cache_dir"] == str(cache)
    assert payload["cache_db"].endswith("cache.sqlite")
    assert payload["pdf_dir"].endswith("pdfs")
    assert payload["mode"] in {"dev", "installed"}


def test_config_edit_honours_editor(
    isolated_env: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`config edit` spawns the editor named in $EDITOR with the env_file path."""
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], check: bool = False) -> None:
        captured.append(cmd)

    import quelle.cli.config as cfg

    monkeypatch.setattr(cfg.subprocess, "run", _fake_run)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "my-fake-editor")

    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 0
    assert captured, "subprocess.run was not called"
    assert captured[0][0] == "my-fake-editor"
    # The second argument is the .env path.
    assert captured[0][1].endswith(".env")
    # The file should have been seeded because it did not exist.
    assert Path(captured[0][1]).exists()


def test_config_edit_visual_beats_editor(
    isolated_env: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    import quelle.cli.config as cfg

    monkeypatch.setattr(cfg.subprocess, "run", lambda cmd, check=False: captured.append(cmd))
    monkeypatch.setenv("VISUAL", "visual-editor")
    monkeypatch.setenv("EDITOR", "editor-editor")

    runner.invoke(app, ["config", "edit"])
    assert captured[0][0] == "visual-editor"
