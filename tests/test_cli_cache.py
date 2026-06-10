"""CLI happy-path tests for `quelle cache list / show / clear`.

The cache is seeded through the real Cache API against a temp SQLite
file (no network, no mocking of the cache layer), then driven through
the CLI exactly as a user would.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quelle.cli.main import app
from quelle.models.publication import Author, Publication
from quelle.repositories.cache import Cache

runner = CliRunner()


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")


def _seed_cache(tmp_path: Path) -> Path:
    """Insert two publications into the cache the CLI will open."""
    db = tmp_path / "cache" / "cache.sqlite"
    with Cache.open(db) as cache:
        cache.upsert(
            Publication(
                title="Attention Is All You Need",
                authors=[Author(name="Ashish Vaswani")],
                year=2017,
                doi="10.48550/arxiv.1706.03762",
                kind="article",
            )
        )
        cache.upsert(
            Publication(
                title="Cannibal Capitalism",
                authors=[Author(name="Nancy Fraser")],
                year=2022,
                isbn_13="9781839761232",
                kind="book",
            )
        )
    return db


def test_cache_list_json_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    db = _seed_cache(tmp_path)
    result = runner.invoke(app, ["--json", "cache", "list"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total"] == 2
    assert payload["cache_db"] == str(db)
    keys = {entry["citation_key"] for entry in payload["entries"]}
    assert keys == {"Vaswani2017", "Fraser2022"}


def test_cache_list_rich_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_cache(tmp_path)
    result = runner.invoke(app, ["cache", "list"])
    assert result.exit_code == 0
    assert "2 entries" in result.output
    # Citekey column values; the wider DOI/title columns wrap at 80 cols,
    # so only assert on the short, contiguous cells.
    assert "Vaswani2017" in result.output
    assert "Fraser2022" in result.output


def test_cache_list_honours_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--limit` caps the listed rows while the header keeps the true total."""
    _env(monkeypatch, tmp_path)
    _seed_cache(tmp_path)
    result = runner.invoke(app, ["--json", "cache", "list", "--limit", "1"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total"] == 2
    assert len(payload["entries"]) == 1


def test_cache_show_rich_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_cache(tmp_path)
    result = runner.invoke(app, ["cache", "show", "10.48550/arxiv.1706.03762"])
    assert result.exit_code == 0
    assert "Attention Is All You Need" in result.output
    assert "Ashish Vaswani" in result.output
    assert "cite:Vaswani2017" in result.output


def test_cache_show_by_isbn_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_cache(tmp_path)
    result = runner.invoke(app, ["--json", "cache", "show", "9781839761232"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["title"] == "Cannibal Capitalism"
    assert payload["citation_key"] == "Fraser2022"


def test_cache_show_corrupt_cache_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`cache show` maps a CacheError to the documented exit 3, like list."""
    _env(monkeypatch, tmp_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "cache.sqlite").write_bytes(b"this is not a sqlite database " * 20)
    result = runner.invoke(app, ["cache", "show", "10.1/x"])
    assert result.exit_code == 3


def test_cache_clear_corrupt_cache_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`cache clear --yes` maps a CacheError to the documented exit 3."""
    _env(monkeypatch, tmp_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "cache.sqlite").write_bytes(b"this is not a sqlite database " * 20)
    result = runner.invoke(app, ["cache", "clear", "--yes"])
    assert result.exit_code == 3


def test_cache_clear_requires_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --yes the wipe is refused and the rows survive."""
    _env(monkeypatch, tmp_path)
    db = _seed_cache(tmp_path)
    result = runner.invoke(app, ["cache", "clear"])
    assert result.exit_code == 1
    with Cache.open(db) as cache:
        assert cache.stats()["total"] == 2


def test_cache_clear_with_yes_wipes_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    db = _seed_cache(tmp_path)
    result = runner.invoke(app, ["--json", "cache", "clear", "--yes"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"cleared_rows": 2}
    with Cache.open(db) as cache:
        assert cache.stats()["total"] == 0


def test_cache_clear_rich_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_cache(tmp_path)
    result = runner.invoke(app, ["cache", "clear", "--yes"])
    assert result.exit_code == 0
    assert "cleared_rows" in result.output
    assert "2" in result.output
