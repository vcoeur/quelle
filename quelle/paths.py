"""Resolve config, data, and cache directories for the CLI.

Uses `platformdirs` so the layout follows OS conventions: XDG on Linux,
`~/Library/Application Support` + `~/Library/Caches` on macOS, `%APPDATA%` +
`%LOCALAPPDATA%` on Windows. Env-var overrides win over everything so tests,
Docker, and power users can point the three locations anywhere.

Dev mode is detected by walking up from `__file__` looking for `pyproject.toml`:
when running from a source checkout the `.env` at the repo root is still the
config source (convenient during development), but cache and downloaded PDFs
go into a repo-local `.dev-state/` subdirectory so dev state never mixes with
the user's real installed data.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

APP_NAME = "quelle"

ENV_CONFIG_DIR = "QUELLE_CONFIG_DIR"
ENV_DATA_DIR = "QUELLE_DATA_DIR"
ENV_CACHE_DIR = "QUELLE_CACHE_DIR"


@dataclass(frozen=True)
class Paths:
    """Resolved filesystem locations for a single CLI invocation."""

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    env_file: Path
    pdf_dir: Path
    cache_db: Path
    is_dev: bool
    # True only when every location is the default platformdirs target —
    # no QUELLE_*_DIR override active and not running in dev mode. The
    # legacy-layout migration keys off this so it never relocates real
    # user data into a throwaway override dir or a repo's `.dev-state/`.
    is_default: bool


def _looks_like_installed_location(path: Path) -> bool:
    """True when `path` is inside a uv tools venv or any site-packages tree."""
    parts = path.parts
    return "site-packages" in parts or ("uv" in parts and "tools" in parts)


def _declares_quelle(pyproject: Path) -> bool:
    """True when `pyproject` parses as TOML and declares `name = "quelle"`.

    Guards dev-mode detection against foreign projects: a vendored copy of
    quelle under someone else's repo finds *their* `pyproject.toml` first,
    and treating it as quelle's would seed `.env` / `.dev-state/` there.
    A parse error counts as "not quelle".
    """
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    project = data.get("project")
    return isinstance(project, dict) and project.get("name") == "quelle"


def _repo_root() -> Path | None:
    """Return the repo root when running from a source checkout, else None.

    Only a `pyproject.toml` that actually declares quelle counts; foreign
    pyprojects above a vendored install are skipped.
    """
    here = Path(__file__).resolve()
    if _looks_like_installed_location(here):
        return None
    for parent in here.parents:
        candidate = parent / "pyproject.toml"
        if candidate.exists() and _declares_quelle(candidate):
            return parent
    return None


def resolve() -> Paths:
    """Compute the effective config/data/cache layout for this process."""
    repo = _repo_root()

    if repo is not None:
        dev_config = repo
        dev_data = repo / ".dev-state"
        dev_cache = repo / ".dev-state" / "cache"
    else:
        dev_config = dev_data = dev_cache = None

    installed_config = Path(user_config_dir(APP_NAME, appauthor=False))
    installed_data = Path(user_data_dir(APP_NAME, appauthor=False))
    installed_cache = Path(user_cache_dir(APP_NAME, appauthor=False))

    def pick(env_var: str, dev: Path | None, installed: Path) -> Path:
        override = os.environ.get(env_var)
        if override:
            return Path(override).expanduser()
        return dev if dev is not None else installed

    config_dir = pick(ENV_CONFIG_DIR, dev_config, installed_config)
    data_dir = pick(ENV_DATA_DIR, dev_data, installed_data)
    cache_dir = pick(ENV_CACHE_DIR, dev_cache, installed_cache)

    any_override = any(
        os.environ.get(env_var) for env_var in (ENV_CONFIG_DIR, ENV_DATA_DIR, ENV_CACHE_DIR)
    )

    return Paths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        env_file=config_dir / ".env",
        pdf_dir=data_dir / "pdfs",
        cache_db=cache_dir / "cache.sqlite",
        is_dev=repo is not None,
        is_default=repo is None and not any_override,
    )


def ensure_dirs(paths: Paths) -> None:
    """Create config, data, and cache directories if missing. Idempotent."""
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.pdf_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
