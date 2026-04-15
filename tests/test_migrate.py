"""Tests for the one-shot legacy-layout migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from quelle.migrate import migrate_legacy_layout
from quelle.paths import Paths


def _paths_under(tmp_path: Path) -> Paths:
    return Paths(
        config_dir=tmp_path / "new_cfg",
        data_dir=tmp_path / "new_data",
        cache_dir=tmp_path / "new_cache",
        env_file=tmp_path / "new_cfg" / ".env",
        pdf_dir=tmp_path / "new_data" / "pdfs",
        cache_db=tmp_path / "new_cache" / "cache.sqlite",
        is_dev=False,
    )


def _seed_legacy(home: Path, *, env: bool = True, cache: bool = True, pdfs: bool = True) -> None:
    """Seed whichever legacy artifacts the caller asks for under `home`."""
    if env:
        legacy_cfg = home / ".config" / "publications"
        legacy_cfg.mkdir(parents=True)
        (legacy_cfg / ".env").write_text("QUELLE_CONTACT_EMAIL=legacy@example.com\n")
    if cache or pdfs:
        legacy_state = home / ".publications" / ".publications-state"
        legacy_state.mkdir(parents=True)
        if cache:
            (legacy_state / "cache.sqlite").write_bytes(b"legacy-sqlite-bytes")
        if pdfs:
            (legacy_state / "pdfs").mkdir()
            (legacy_state / "pdfs" / "sample.pdf").write_bytes(b"%PDF-legacy")


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point `Path.home()` at a throwaway directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PUBLICATIONS_HOME", raising=False)
    return home


def test_migrates_env_file(fake_home: Path, tmp_path: Path) -> None:
    _seed_legacy(fake_home, cache=False, pdfs=False)
    paths = _paths_under(tmp_path / "new")

    moved = migrate_legacy_layout(paths)

    assert len(moved) == 1
    assert paths.env_file.exists()
    assert paths.env_file.read_text() == "QUELLE_CONTACT_EMAIL=legacy@example.com\n"
    assert not (fake_home / ".config" / "publications" / ".env").exists()
    # Empty legacy parent dir is cleaned up.
    assert not (fake_home / ".config" / "publications").exists()


def test_migrates_cache_db(fake_home: Path, tmp_path: Path) -> None:
    _seed_legacy(fake_home, env=False, pdfs=False)
    paths = _paths_under(tmp_path / "new")

    moved = migrate_legacy_layout(paths)

    assert len(moved) == 1
    assert paths.cache_db.exists()
    assert paths.cache_db.read_bytes() == b"legacy-sqlite-bytes"
    assert not (fake_home / ".publications" / ".publications-state" / "cache.sqlite").exists()


def test_migrates_pdfs_dir(fake_home: Path, tmp_path: Path) -> None:
    _seed_legacy(fake_home, env=False, cache=False)
    paths = _paths_under(tmp_path / "new")

    moved = migrate_legacy_layout(paths)

    assert len(moved) == 1
    assert paths.pdf_dir.is_dir()
    assert (paths.pdf_dir / "sample.pdf").read_bytes() == b"%PDF-legacy"
    assert not (fake_home / ".publications" / ".publications-state" / "pdfs").exists()


def test_migrates_everything(fake_home: Path, tmp_path: Path) -> None:
    _seed_legacy(fake_home)
    paths = _paths_under(tmp_path / "new")

    moved = migrate_legacy_layout(paths)

    assert len(moved) == 3
    assert paths.env_file.exists()
    assert paths.cache_db.exists()
    assert (paths.pdf_dir / "sample.pdf").exists()
    # Legacy dirs cleaned up.
    assert not (fake_home / ".publications" / ".publications-state").exists()
    assert not (fake_home / ".publications").exists()


def test_second_call_is_noop(fake_home: Path, tmp_path: Path) -> None:
    _seed_legacy(fake_home)
    paths = _paths_under(tmp_path / "new")

    first = migrate_legacy_layout(paths)
    second = migrate_legacy_layout(paths)

    assert len(first) == 3
    assert second == []


def test_skips_when_target_env_exists(fake_home: Path, tmp_path: Path) -> None:
    _seed_legacy(fake_home, cache=False, pdfs=False)
    paths = _paths_under(tmp_path / "new")
    paths.config_dir.mkdir(parents=True)
    paths.env_file.write_text("existing=1\n")

    moved = migrate_legacy_layout(paths)

    assert moved == []
    # Existing target is untouched.
    assert paths.env_file.read_text() == "existing=1\n"
    # Legacy is left where it was (not moved, not deleted).
    assert (fake_home / ".config" / "publications" / ".env").exists()


def test_skips_when_nothing_legacy(fake_home: Path, tmp_path: Path) -> None:
    paths = _paths_under(tmp_path / "new")
    moved = migrate_legacy_layout(paths)
    assert moved == []


def test_no_crash_when_move_fails(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If shutil.move raises OSError, the CLI must not crash."""
    _seed_legacy(fake_home, cache=False, pdfs=False)
    paths = _paths_under(tmp_path / "new")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated")

    import quelle.migrate as mig

    monkeypatch.setattr(mig.shutil, "move", _boom)

    moved = migrate_legacy_layout(paths)

    assert moved == []
    # Legacy still there, but the CLI did not raise.
    assert (fake_home / ".config" / "publications" / ".env").exists()
