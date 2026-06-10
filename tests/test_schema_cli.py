"""Tests for `quelle schema` — valid JSON contract listing the new surface."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from quelle.cli.introspect import command_listing
from quelle.cli.main import app
from quelle.services.schema import build_schema

runner = CliRunner()


def _schema() -> dict:
    return build_schema(commands=command_listing(app))


def test_build_schema_lists_new_commands_and_tables() -> None:
    schema = _schema()
    names = {c["name"] for c in schema["commands"]}
    assert {"resolve", "schema", "skill", "fetch", "search", "cache", "config"} <= names
    assert schema["kind_map"]["preprint"] == "article"
    assert schema["kind_map"]["web"] == "web"
    assert "web" in schema["kinds"] and "media" in schema["kinds"]
    assert "citekey" in schema["x_vcoeur"]
    assert any(f["name"] == "title" for f in schema["source_fields"])
    assert {e["code"] for e in schema["exit_codes"]} == {0, 1, 2, 3, 4, 64}


def test_schema_resolve_command_exposes_flags() -> None:
    schema = _schema()
    resolve = next(c for c in schema["commands"] if c["name"] == "resolve")
    flags = {flag for p in resolve["params"] for flag in p.get("flags", [])}
    assert "--taken" in flags
    assert "--taken-file" in flags
    assert "--csl" in flags
    assert "--download-pdf" in flags


def test_schema_service_does_not_import_the_cli() -> None:
    """Layering: `services.schema` must stay below `quelle.cli` — the
    introspection half lives in `quelle.cli.introspect` instead."""
    import ast
    import inspect

    from quelle.services import schema as schema_module

    tree = ast.parse(inspect.getsource(schema_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {name for name in imported if name.startswith("quelle.cli")}


def test_schema_cli_emits_valid_json() -> None:
    result = runner.invoke(app, ["--json", "schema"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["tool"] == "quelle"
    assert any(c["name"] == "resolve" for c in payload["commands"])
    assert {e["code"] for e in payload["exit_codes"]} == {0, 1, 2, 3, 4, 64}


def test_schema_cli_tty_summary() -> None:
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert "quelle" in result.output
    assert "kind map" in result.output
