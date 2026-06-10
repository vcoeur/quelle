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
import shlex
import subprocess
from dataclasses import fields
from typing import Any

import typer

from quelle.cli._helpers import exit_code_for, load_settings_or_exit, report_error
from quelle.cli.output import OutputMode, render_config
from quelle.repositories.errors import ConfigError
from quelle.settings import Settings

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

# Google Books API key (console.cloud.google.com -> APIs & Services ->
# Credentials, then enable the Books API). Unauthenticated calls cap at
# 1 000 requests/day per IP; the key raises that ceiling.
#GOOGLE_BOOKS_API_KEY=

# Override the HTTP User-Agent. Defaults to quelle/<version>, with
# (+mailto:QUELLE_CONTACT_EMAIL) appended when the email is set.
#QUELLE_USER_AGENT=

# HTTP timeout per request, in seconds.
#QUELLE_HTTP_TIMEOUT=30

# Maximum PDF size to download, in megabytes.
#QUELLE_MAX_PDF_MB=100
"""


# Path-derived rows that go at the top of the `quelle config` output.
# Keyed before the `Settings` field-walk so the user sees their layout
# first; secret-bearing settings render with redaction applied.
_PATH_FIELDS = ("config_dir", "data_dir", "cache_dir", "env_file", "cache_db", "pdf_dir")

# `Settings` fields surfaced in `quelle config`. Any new field added to
# `Settings` is silently absent from the rendered output until added
# here — kept explicit on purpose so secrets aren't surfaced by accident.
# Format: setting name → display formatter (None → use the raw value).
_SETTINGS_DISPLAY: dict[str, Any] = {
    "openalex_api_key": lambda s: s.openalex_key_redacted or "(unset)",
    "semantic_scholar_api_key": lambda s: "(set)" if s.semantic_scholar_api_key else "(unset)",
    "google_books_api_key": lambda s: "(set)" if s.google_books_api_key else "(unset)",
    "unpaywall_email": lambda s: s.unpaywall_email or "(unset)",
    "contact_email": lambda s: s.contact_email or "(unset)",
    "user_agent": None,
    "http_timeout": None,
    "max_pdf_mb": None,
}


def _full_config_payload(settings: Settings) -> dict[str, Any]:
    """Build the dict shown by bare `quelle config` (all values + paths).

    The `Settings` half is field-list-driven over `_SETTINGS_DISPLAY`
    rather than hand-listed, so adding a new field to `Settings` plus
    one entry here keeps `quelle config` truthful — and secrets stay
    redacted. The path block is hand-listed because `Paths` is purely
    derived state and the order matters for human readability.
    """
    settings_field_names = {f.name for f in fields(Settings)}
    unknown = set(_SETTINGS_DISPLAY) - settings_field_names
    if unknown:
        # Programmer error — `_SETTINGS_DISPLAY` references a removed field.
        raise RuntimeError(f"_SETTINGS_DISPLAY references unknown Settings: {sorted(unknown)}")

    p = settings.paths
    payload: dict[str, Any] = {
        "mode": "dev" if p.is_dev else "installed",
        **{name: str(getattr(p, name)) for name in _PATH_FIELDS},
    }
    for name, formatter in _SETTINGS_DISPLAY.items():
        payload[name] = formatter(settings) if formatter else getattr(settings, name)
    return payload


@config_app.callback(invoke_without_command=True)
def _config_root(ctx: typer.Context) -> None:
    """Show the effective configuration when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    settings = load_settings_or_exit()
    render_config(_full_config_payload(settings), mode=OutputMode.from_ctx(ctx))


@config_app.command("edit")
def config_edit() -> None:
    """Open the quelle .env file in $VISUAL / $EDITOR or the OS default editor.

    Seeds the .env from the bundled template when the file does not yet
    exist; prints a one-line "created" hint in that case so the user
    knows the editor is opening a fresh template, not their previous
    edits.
    """
    settings = load_settings_or_exit()
    env_file = settings.paths.env_file
    created = _ensure_env_file(settings)
    editor = _resolve_editor()
    # A multi-word $EDITOR ("code --wait") is a command line, not a single
    # executable name — split it like a shell would.
    argv = shlex.split(editor)
    if not argv:
        _editor_failed(ConfigError(f"editor command is empty: {editor!r}"))
    if created:
        typer.echo(f"Created {env_file} from the default template.")
        typer.echo("Set QUELLE_CONTACT_EMAIL for the Crossref / OpenAlex polite pool.")
    typer.echo(f"Opening {env_file} in {editor!r}")
    try:
        subprocess.run([*argv, str(env_file)], check=False)
    except OSError as exc:
        _editor_failed(ConfigError(f"could not start editor {editor!r}: {exc}"))


def _editor_failed(exc: ConfigError) -> None:
    """Report a broken $VISUAL / $EDITOR value and exit with the config code."""
    report_error(exc)
    raise typer.Exit(exit_code_for(exc)) from exc


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
