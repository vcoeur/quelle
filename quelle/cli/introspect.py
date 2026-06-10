"""Typer/Click introspection for `quelle schema` — the CLI-layer half.

Commands and flags are introspected from the live Typer app via duck-typed
attribute access (not `isinstance` against `click`), so the listing never
drifts from the real surface and survives typer >= 0.25 vendoring its own
copy of click. The static contract tables live in
`quelle.services.schema`, which takes this module's output as data — the
services layer never imports the CLI.
"""

from __future__ import annotations

from typing import Any

import typer


def _param_info(param: Any) -> dict[str, Any] | None:
    """Describe a single Click parameter, or None for things we don't surface.

    Duck-typed on `param.param_type_name` ("argument" / "option") instead of
    `isinstance` against `click`: typer >= 0.25 vendors its own copy of click,
    so an introspected param is not an instance of a separately-imported
    `click`'s classes — an isinstance check silently returns False there and
    the whole surface drops out of the schema.
    """
    kind = getattr(param, "param_type_name", None)
    if kind == "argument":
        return {"name": param.name, "kind": "argument", "required": param.required}
    if kind == "option":
        return {
            "name": param.name,
            "kind": "option",
            "flags": list(param.opts),
            "required": param.required,
            "is_flag": getattr(param, "is_flag", False),
            "multiple": getattr(param, "multiple", False),
            "help": (getattr(param, "help", "") or "").strip(),
        }
    return None


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n", 1)[0].strip()


def _command_info(name: str, cmd: Any) -> dict[str, Any]:
    """Describe a command (and one level of subcommands for groups).

    A group is detected by a populated `.commands` dict rather than an
    `isinstance(cmd, click.Group)` check (see `_param_info` for why).
    """
    info: dict[str, Any] = {
        "name": name,
        "help": _first_line(cmd.help or cmd.short_help),
        "params": [p for p in (_param_info(pp) for pp in cmd.params) if p],
    }
    subcommands = getattr(cmd, "commands", None)
    if isinstance(subcommands, dict) and subcommands:
        info["subcommands"] = [_command_info(sub, subcommands[sub]) for sub in sorted(subcommands)]
    return info


def command_listing(app: typer.Typer) -> list[dict[str, Any]]:
    """Introspect every command (and subcommand) of the live Typer app."""
    cli = typer.main.get_command(app)
    cli_commands = getattr(cli, "commands", {})
    return [_command_info(name, cli_commands[name]) for name in sorted(cli_commands)]
