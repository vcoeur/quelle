"""One-shot migration from the legacy PublicationManager layout.

On first upgrade from PublicationManager — where config lived at
`~/.config/publications/.env` and cache + PDFs were bundled under
`~/.publications/.publications-state/` — move the user's files into the new
platformdirs layout so nothing is lost. Each move is guarded on target
absence so running the migration twice does nothing.

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

    Returns short human-readable descriptions of each move performed. On
    per-file failure, logs a warning and continues. Silent when nothing
    needs moving.
    """
    moved: list[str] = []

    legacy_env = _legacy_env_file()
    legacy_state = _legacy_state_dir()
    legacy_cache_db = legacy_state / "cache.sqlite"
    legacy_pdfs = legacy_state / "pdfs"

    if legacy_env.exists() and not paths_obj.env_file.exists():
        try:
            paths_obj.config_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_env), str(paths_obj.env_file))
            moved.append(f"{legacy_env} -> {paths_obj.env_file}")
            _rmdir_if_empty(legacy_env.parent)
        except OSError as exc:
            _warn(f"could not migrate {legacy_env}: {exc}")

    if legacy_cache_db.exists() and not paths_obj.cache_db.exists():
        try:
            paths_obj.cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_cache_db), str(paths_obj.cache_db))
            moved.append(f"{legacy_cache_db} -> {paths_obj.cache_db}")
        except OSError as exc:
            _warn(f"could not migrate {legacy_cache_db}: {exc}")

    if legacy_pdfs.exists() and not paths_obj.pdf_dir.exists():
        try:
            paths_obj.data_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_pdfs), str(paths_obj.pdf_dir))
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
