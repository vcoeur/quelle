"""Tests for the `quelle config` sub-app."""

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


def test_config_root_creates_dirs(isolated_env: tuple[Path, Path, Path]) -> None:
    """Bare `quelle config` runs ensure_dirs via load_settings()."""
    config, data, cache = isolated_env
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert config.is_dir()
    assert (data / "pdfs").is_dir()
    assert cache.is_dir()


def test_config_root_json_payload(isolated_env: tuple[Path, Path, Path]) -> None:
    config, data, cache = isolated_env
    result = runner.invoke(app, ["--json", "config"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["config_dir"] == str(config)
    assert payload["data_dir"] == str(data)
    assert payload["cache_dir"] == str(cache)
    assert payload["cache_db"].endswith("cache.sqlite")
    assert payload["pdf_dir"].endswith("pdfs")
    assert payload["mode"] in {"dev", "installed"}


def test_config_edit_seeds_env_on_first_run(
    isolated_env: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First `config edit` writes the default .env and prints a 'created' line."""
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], check: bool = False) -> None:
        captured.append(cmd)

    import quelle.cli.config as cfg

    monkeypatch.setattr(cfg.subprocess, "run", _fake_run)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "my-fake-editor")

    config, _data, _cache = isolated_env
    env_file = config / ".env"
    assert not env_file.exists()
    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 0
    assert env_file.exists()
    assert "QUELLE_CONTACT_EMAIL" in env_file.read_text()
    assert "Created" in result.output
    # Editor should have been spawned with the env_file path.
    assert captured and captured[0][0] == "my-fake-editor"
    assert captured[0][1] == str(env_file)


def test_config_edit_no_seed_when_env_exists(
    isolated_env: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the .env already exists, edit does not overwrite or print 'created'."""
    config, _data, _cache = isolated_env
    env_file = config / ".env"
    config.mkdir(parents=True, exist_ok=True)
    env_file.write_text("QUELLE_CONTACT_EMAIL=first@example.com\n")

    import quelle.cli.config as cfg

    monkeypatch.setattr(cfg.subprocess, "run", lambda cmd, check=False: None)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "my-fake-editor")

    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 0
    assert env_file.read_text() == "QUELLE_CONTACT_EMAIL=first@example.com\n"
    assert "Created" not in result.output


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


def test_config_edit_splits_multi_word_editor(
    isolated_env: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EDITOR="code --wait"` is a command line, not one executable name."""
    captured: list[list[str]] = []

    import quelle.cli.config as cfg

    monkeypatch.setattr(cfg.subprocess, "run", lambda cmd, check=False: captured.append(cmd))
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "code --wait")

    config, _data, _cache = isolated_env
    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 0
    assert captured[0][:2] == ["code", "--wait"]
    assert captured[0][2] == str(config / ".env")


def test_config_edit_missing_editor_is_clean_config_error(
    isolated_env: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spawn failure exits with the config code, not a traceback."""

    import quelle.cli.config as cfg

    def _boom(cmd: list[str], check: bool = False) -> None:
        raise FileNotFoundError(f"No such file or directory: {cmd[0]!r}")

    monkeypatch.setattr(cfg.subprocess, "run", _boom)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "definitely-not-an-editor")

    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 4
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_seeded_env_template_lists_documented_keys(
    isolated_env: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seeded template stays consistent with .env.example."""
    import quelle.cli.config as cfg

    monkeypatch.setattr(cfg.subprocess, "run", lambda cmd, check=False: None)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "my-fake-editor")

    config, _data, _cache = isolated_env
    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 0
    seeded = (config / ".env").read_text()
    for key in (
        "QUELLE_CONTACT_EMAIL",
        "OPENALEX_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "UNPAYWALL_EMAIL",
        "GOOGLE_BOOKS_API_KEY",
        "QUELLE_USER_AGENT",
        "QUELLE_HTTP_TIMEOUT",
        "QUELLE_MAX_PDF_MB",
    ):
        assert key in seeded, f"{key} missing from the seeded .env template"


def test_malformed_env_file_value_exits_4(
    isolated_env: tuple[Path, Path, Path],
) -> None:
    """A bad value in the .env file is a ConfigError (exit 4), not a traceback."""
    import os

    config, _data, _cache = isolated_env
    config.mkdir(parents=True, exist_ok=True)
    (config / ".env").write_text("QUELLE_MAX_PDF_MB=lots\n")

    try:
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 4
        assert result.exception is None or isinstance(result.exception, SystemExit)
    finally:
        # `env.read_env` loads the .env into os.environ; scrub the bad value
        # so it cannot leak into later tests.
        os.environ.pop("QUELLE_MAX_PDF_MB", None)
