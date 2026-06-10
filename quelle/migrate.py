"""One-shot migration from the legacy config/cache layout.

Where config lived at `~/.config/publications/.env` and cache + PDFs were
bundled under `~/.publications/.publications-state/`, move the user's files
into the new platformdirs layout so nothing is lost. Each move is guarded on
target absence so running the migration twice does nothing.

The migration only runs when the resolved paths are the default platformdirs
targets (`Paths.is_default`): with a `QUELLE_*_DIR` override active or in dev
mode the current invocation points at a throwaway / repo-local layout, and
relocating real user data there would lose it.

Each move is atomic with respect to the target: the source is copied to a
temp name inside the target directory, renamed into place, and only then
deleted. An interrupted run never leaves a partial file under the real
target name, so the target-absence guard stays safe to re-run.

Called from `load_settings()` before `ensure_dirs()`. Never raises; any
filesystem error is logged as a warning and the CLI continues.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from quelle.paths import Paths


def _legacy_env_file() -> Path:
    return Path.home() / ".config" / "publications" / ".env"


def _legacy_state_dir() -> Path:
    return Path.home() / ".publications" / ".publications-state"


def migrate_legacy_layout(paths_obj: Paths) -> list[str]:
    """Move legacy artifacts into the new layout. Idempotent.

    No-op unless `paths_obj.is_default` (see module docstring). Returns
    short human-readable descriptions of each move performed. On per-file
    failure, logs a warning and continues. Silent when nothing needs
    moving.
    """
    if not paths_obj.is_default:
        return []

    moved: list[str] = []

    legacy_env = _legacy_env_file()
    legacy_state = _legacy_state_dir()
    legacy_cache_db = legacy_state / "cache.sqlite"
    legacy_pdfs = legacy_state / "pdfs"

    if legacy_env.exists() and not paths_obj.env_file.exists():
        try:
            paths_obj.config_dir.mkdir(parents=True, exist_ok=True)
            _move_file(legacy_env, paths_obj.env_file)
            moved.append(f"{legacy_env} -> {paths_obj.env_file}")
            _rmdir_if_empty(legacy_env.parent)
        except OSError as exc:
            _warn(f"could not migrate {legacy_env}: {exc}")

    if legacy_cache_db.exists() and not paths_obj.cache_db.exists():
        try:
            paths_obj.cache_dir.mkdir(parents=True, exist_ok=True)
            _move_file(legacy_cache_db, paths_obj.cache_db)
            moved.append(f"{legacy_cache_db} -> {paths_obj.cache_db}")
        except OSError as exc:
            _warn(f"could not migrate {legacy_cache_db}: {exc}")

    if legacy_pdfs.exists() and not paths_obj.pdf_dir.exists():
        try:
            paths_obj.data_dir.mkdir(parents=True, exist_ok=True)
            _move_dir(legacy_pdfs, paths_obj.pdf_dir)
            moved.append(f"{legacy_pdfs} -> {paths_obj.pdf_dir}")
        except OSError as exc:
            _warn(f"could not migrate {legacy_pdfs}: {exc}")

    _rmdir_if_empty(legacy_state)
    _rmdir_if_empty(legacy_state.parent)

    if moved and os.environ.get("PUBLICATIONS_HOME"):
        _warn(
            "PUBLICATIONS_HOME is set but obsolete — quelle ignores it. "
            "Use QUELLE_CONFIG_DIR / QUELLE_DATA_DIR / QUELLE_CACHE_DIR instead."
        )

    for description in moved:
        _info(f"migrated: {description}")

    return moved


def _tmp_sibling(target: Path) -> Path:
    """A temp name next to `target`, on the same filesystem so rename is atomic."""
    return target.with_name(f"{target.name}.tmp-{os.getpid()}")


def _move_file(source: Path, target: Path) -> None:
    """Move `source` to `target` without ever exposing a partial target.

    `shutil.move` across filesystems is copy-then-delete: an interruption
    leaves a partial target that the caller's absence guard then skips
    forever. Copy to a temp sibling, atomically rename into place, then
    remove the source.
    """
    tmp = _tmp_sibling(target)
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    source.unlink()


def _move_dir(source: Path, target: Path) -> None:
    """Move the directory `source` to `target`; same guarantees as `_move_file`."""
    tmp = _tmp_sibling(target)
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(source, tmp)
        os.replace(tmp, target)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(source)


def _rmdir_if_empty(path: Path) -> None:
    """Remove `path` only if it exists and is an empty directory."""
    if not path.exists() or not path.is_dir():
        return
    try:
        next(iter(path.iterdir()))
    except StopIteration:
        try:
            path.rmdir()
        except OSError:
            pass
    except OSError:
        pass


def _info(message: str) -> None:
    print(f"quelle: {message}", file=sys.stderr)


def _warn(message: str) -> None:
    print(f"quelle: warning: {message}", file=sys.stderr)
