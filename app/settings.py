"""Runtime configuration loaded from environment and .env files.

Local state lives under `PUBLICATIONS_HOME`. Discovery order mirrors
KastenManager so the same codebase works in three runtime contexts:

  1. **Dev from the repo** (`uv run publications …`, `make ...`):
     `_default_home()` walks up from `__file__` and finds the repo root
     via `pyproject.toml`. The repo's `.env` is picked up automatically.
  2. **Installed globally** (`uv tool install .`): `__file__` is inside
     a uv tools venv's `site-packages/`, so the walk is skipped and
     `_default_home()` falls back to `~/.publications`. `.env` can still
     be dropped into `~/.config/publications/.env`.
  3. **Tests**: the `tmp_settings` fixture constructs `Settings`
     explicitly; the discovery helpers are bypassed entirely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from environs import Env

USER_CONFIG_ENV = Path.home() / ".config" / "publications" / ".env"
FALLBACK_HOME = Path.home() / ".publications"


def _looks_like_installed_location(path: Path) -> bool:
    """True when `path` is inside a uv tools venv (or any site-packages tree)."""
    parts = path.parts
    return "site-packages" in parts or ("uv" in parts and "tools" in parts)


def _default_home() -> Path:
    """Return the best guess for `PUBLICATIONS_HOME` when nothing overrides it."""
    here = Path(__file__).resolve()
    if not _looks_like_installed_location(here):
        for parent in here.parents:
            if (parent / "pyproject.toml").exists():
                return parent
    return FALLBACK_HOME


@dataclass(frozen=True)
class Settings:
    """Effective configuration for a single CLI invocation."""

    openalex_api_key: str
    semantic_scholar_api_key: str
    unpaywall_email: str
    contact_email: str
    http_timeout: float
    user_agent: str
    max_pdf_mb: int
    home: Path
    state_dir: Path
    cache_db: Path
    pdf_dir: Path

    @property
    def openalex_key_redacted(self) -> str:
        """Key prefix with the rest masked, for display."""
        key = self.openalex_api_key
        if not key:
            return ""
        return f"{key[:4]}******" if len(key) > 4 else "******"


def _env_file_candidates(explicit: Path | None) -> list[Path]:
    """Return every `.env` that should be layered, in priority order."""
    candidates: list[Path] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.expanduser()
        if resolved.exists() and resolved not in candidates:
            candidates.append(resolved)

    add(explicit)
    add(USER_CONFIG_ENV)

    env_home_value = os.environ.get("PUBLICATIONS_HOME")
    if env_home_value:
        add(Path(env_home_value) / ".env")

    add(_default_home() / ".env")
    return candidates


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from process env and optional .env file."""
    env = Env()
    for candidate in _env_file_candidates(env_file):
        env.read_env(str(candidate), override=False)

    home = Path(env.str("PUBLICATIONS_HOME", str(_default_home()))).expanduser().resolve()
    state_dir = home / env.str("PUBLICATIONS_STATE_DIR", ".publications-state")
    contact_email = env.str("PUBLICATIONS_CONTACT_EMAIL", "")
    default_agent = (
        f"PublicationManager/0.1.0 (+mailto:{contact_email})"
        if contact_email
        else "PublicationManager/0.1.0"
    )

    return Settings(
        openalex_api_key=env.str("OPENALEX_API_KEY", ""),
        semantic_scholar_api_key=env.str("SEMANTIC_SCHOLAR_API_KEY", ""),
        unpaywall_email=env.str("UNPAYWALL_EMAIL", contact_email),
        contact_email=contact_email,
        http_timeout=env.float("PUBLICATIONS_HTTP_TIMEOUT", 30.0),
        user_agent=env.str("PUBLICATIONS_USER_AGENT", default_agent),
        max_pdf_mb=env.int("PUBLICATIONS_MAX_PDF_MB", 100),
        home=home,
        state_dir=state_dir,
        cache_db=state_dir / "cache.sqlite",
        pdf_dir=state_dir / "pdfs",
    )


def ensure_dirs(settings: Settings) -> None:
    """Create state + pdf directories if missing. Idempotent."""
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
