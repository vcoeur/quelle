"""`quelle config` sub-app — inspect and edit the user's configuration.

Three subcommands:

- `quelle config show` — dump the effective configuration (same format as
  before the refactor, now reachable under `show`).
- `quelle config path` — print just the resolved config/data/cache paths.
  Small, grep-friendly output suitable for shell scripts.
- `quelle config edit` — open the `.env` file in `$VISUAL` / `$EDITOR` or the
  OS default editor. On Windows this means users never have to navigate
  `%APPDATA%` manually.

The `init_command` helper is invoked by the top-level `quelle init` command
in `main.py`; it creates the standard directories and seeds a default `.env`
if none exists.
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Any

import typer

from quelle.cli.output import OutputMode, emit_json, render_config
from quelle.settings import Settings, load_settings

config_app = typer.Typer(
    help="Inspect and edit the quelle configuration.",
    no_args_is_help=True,
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
    """Build the dict emitted by `config show` (all values + paths)."""
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


def _paths_payload(settings: Settings) -> dict[str, str]:
    """Build the dict emitted by `config path` (paths only)."""
    p = settings.paths
    return {
        "mode": "dev" if p.is_dev else "installed",
        "config_dir": str(p.config_dir),
        "data_dir": str(p.data_dir),
        "cache_dir": str(p.cache_dir),
        "env_file": str(p.env_file),
        "cache_db": str(p.cache_db),
        "pdf_dir": str(p.pdf_dir),
    }


@config_app.command("show")
def config_show(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Show the effective configuration (env + .env layers)."""
    settings = load_settings()
    mode = OutputMode.detect(json_output)
    render_config(_full_config_payload(settings), mode=mode)


@config_app.command("path")
def config_path(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Print the resolved config, data, and cache directories."""
    settings = load_settings()
    payload = _paths_payload(settings)
    mode = OutputMode.detect(json_output)
    if mode.json:
        emit_json(payload)
    else:
        for key, value in payload.items():
            typer.echo(f"{key}: {value}")


@config_app.command("edit")
def config_edit() -> None:
    """Open the quelle .env file in $VISUAL / $EDITOR or the OS default editor."""
    settings = load_settings()
    env_file = settings.paths.env_file
    created = _ensure_env_file(settings)
    editor = _resolve_editor()
    if created:
        typer.echo(f"Created {env_file} from the default template.")
    typer.echo(f"Opening {env_file} in {editor!r}")
    subprocess.run([editor, str(env_file)], check=False)


def init_command() -> None:
    """Implementation of the top-level `quelle init` command."""
    settings = load_settings()  # already runs migrate + ensure_dirs
    created = _ensure_env_file(settings)
    p = settings.paths
    typer.echo(f"mode: {'dev' if p.is_dev else 'installed'}")
    typer.echo(f"config_dir: {p.config_dir}")
    typer.echo(f"data_dir: {p.data_dir}")
    typer.echo(f"cache_dir: {p.cache_dir}")
    suffix = "(created)" if created else "(already present)"
    typer.echo(f"env_file: {p.env_file} {suffix}")
    if created:
        typer.echo("")
        typer.echo(
            "Next: set QUELLE_CONTACT_EMAIL in the .env (used for the "
            "Crossref / OpenAlex polite pool)."
        )
        typer.echo("Run `quelle config edit` to open it in your editor.")


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
