"""Runtime configuration loaded from environment and the `.env` file.

Filesystem locations (config / data / cache dirs) are resolved in `paths.py`
via `platformdirs`, so the layout follows each OS's conventions out of the box
and the three dirs can be pointed anywhere via `QUELLE_*_DIR` env overrides.

This module loads non-path values — API keys, contact email, HTTP timeout,
user-agent, max PDF size — from the resolved config dir's `.env` file layered
over the process environment (process env wins).
"""

from __future__ import annotations

from dataclasses import dataclass

from environs import Env

from quelle import paths
from quelle.migrate import migrate_legacy_layout
from quelle.paths import Paths


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
    paths: Paths

    @property
    def openalex_key_redacted(self) -> str:
        """Key prefix with the rest masked, for display."""
        key = self.openalex_api_key
        if not key:
            return ""
        return f"{key[:4]}******" if len(key) > 4 else "******"


def load_settings() -> Settings:
    """Resolve paths, layer env + .env, and return an immutable Settings."""
    resolved = paths.resolve()
    migrate_legacy_layout(resolved)
    paths.ensure_dirs(resolved)

    env = Env()
    if resolved.env_file.exists():
        env.read_env(str(resolved.env_file), override=False)

    contact_email = env.str("QUELLE_CONTACT_EMAIL", "")
    default_agent = f"quelle/0.1.0 (+mailto:{contact_email})" if contact_email else "quelle/0.1.0"

    return Settings(
        openalex_api_key=env.str("OPENALEX_API_KEY", ""),
        semantic_scholar_api_key=env.str("SEMANTIC_SCHOLAR_API_KEY", ""),
        unpaywall_email=env.str("UNPAYWALL_EMAIL", contact_email),
        contact_email=contact_email,
        http_timeout=env.float("QUELLE_HTTP_TIMEOUT", 30.0),
        user_agent=env.str("QUELLE_USER_AGENT", default_agent),
        max_pdf_mb=env.int("QUELLE_MAX_PDF_MB", 100),
        paths=resolved,
    )
