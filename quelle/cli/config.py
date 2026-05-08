"""`quelle config` sub-app — inspect and edit the user's configuration.

Two surfaces:

- `quelle config` — dump the effective configuration (env + .env layers,
  resolved paths, and redacted API key). The bare invocation works via the
  Typer callback's `invoke_without_command=True`.
- `quelle config edit` — open the `.env` file in `$VISUAL` / `$EDITOR` or the
  OS default editor. Seeds a default `.env` from the bundled template if the
  file is missing, and prints a one-line "created" hint so the user knows it
  is a brand-new file.
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Any

import typer

from quelle.cli.output import OutputMode, render_config
from quelle.settings import Settings, load_settings

config_app = typer.Typer(
    help="Inspect and edit the quelle configuration. Bare `quelle config` shows everything.",
    invoke_without_command=True,
)


ENV_EXAMPLE_TEMPLATE = """\
# quelle configuration.
# All values are optional — defaults are tight enough that
# `quelle fetch 10.xxx/yyy` works without any config.

# Your email. Goes into the User-Agent and enrolls you in the polite pool
# for both Crossref and OpenAlex — recommended for any production use.
QUELLE_CONTACT_EMAIL=you@example.com

# Free OpenAlex key (openalex.org/settings/api). Unauthenticated calls work
# but have a lower daily quota.
#OPENALEX_API_KEY=

# Free Semantic Scholar key (api.semanticscholar.org/getting-started).
#SEMANTIC_SCHOLAR_API_KEY=

# Unpaywall requires an email. Defaults to QUELLE_CONTACT_EMAIL when unset.
#UNPAYWALL_EMAIL=you@example.com

# HTTP timeout per request, in seconds.
#QUELLE_HTTP_TIMEOUT=30

# Maximum PDF size to download, in megabytes.
#QUELLE_MAX_PDF_MB=100
"""


def _full_config_payload(settings: Settings) -> dict[str, Any]:
    """Build the dict shown by bare `quelle config` (all values + paths)."""
    p = settings.paths
    return {
        "mode": "dev" if p.is_dev else "installed",
        "config_dir": str(p.config_dir),
        "data_dir": str(p.data_dir),
        "cache_dir": str(p.cache_dir),
        "env_file": str(p.env_file),
        "cache_db": str(p.cache_db),
        "pdf_dir": str(p.pdf_dir),
        "openalex_api_key": settings.openalex_key_redacted or "(unset)",
        "unpaywall_email": settings.unpaywall_email or "(unset)",
        "contact_email": settings.contact_email or "(unset)",
        "user_agent": settings.user_agent,
        "http_timeout": settings.http_timeout,
    }


@config_app.callback(invoke_without_command=True)
def _config_root(ctx: typer.Context) -> None:
    """Show the effective configuration when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    settings = load_settings()
    json_flag = bool(ctx.obj and ctx.obj.get("json"))
    render_config(_full_config_payload(settings), mode=OutputMode.detect(json_flag))


@config_app.command("edit")
def config_edit() -> None:
    """Open the quelle .env file in $VISUAL / $EDITOR or the OS default editor.

    Seeds the .env from the bundled template when the file does not yet
    exist; prints a one-line "created" hint in that case so the user
    knows the editor is opening a fresh template, not their previous
    edits.
    """
    settings = load_settings()
    env_file = settings.paths.env_file
    created = _ensure_env_file(settings)
    editor = _resolve_editor()
    if created:
        typer.echo(f"Created {env_file} from the default template.")
        typer.echo("Set QUELLE_CONTACT_EMAIL for the Crossref / OpenAlex polite pool.")
    typer.echo(f"Opening {env_file} in {editor!r}")
    subprocess.run([editor, str(env_file)], check=False)


def _ensure_env_file(settings: Settings) -> bool:
    """Create the .env file from the default template if it does not exist."""
    env_file = settings.paths.env_file
    if env_file.exists():
        return False
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(ENV_EXAMPLE_TEMPLATE)
    return True


def _resolve_editor() -> str:
    """Return the editor command to open text files with."""
    for var in ("VISUAL", "EDITOR"):
        value = os.environ.get(var)
        if value:
            return value
    system = platform.system()
    if system == "Windows":
        return "notepad"
    if system == "Darwin":
        return "open"
    return "xdg-open"
