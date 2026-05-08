"""Smoke tests for the CLI — wire-up only, no real network."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from quelle.cli.main import app

runner = CliRunner()


def test_version_flag_prints_name_and_version() -> None:
    from quelle import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"quelle {__version__}" in result.output


def test_bare_quelle_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code != 0 or "Usage" in result.output


def test_config_root_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`quelle --json config` (bare) shows the full config payload."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["--json", "config"])
    assert result.exit_code == 0
    assert "cache_dir" in result.output
    assert "alice@example.com" in result.output
    # ensure_dirs runs from load_settings -> data/pdfs and cache dirs exist.
    assert (tmp_path / "data" / "pdfs").is_dir()
    assert (tmp_path / "cache").is_dir()


def test_fetch_requires_query() -> None:
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code != 0


def test_search_requires_query() -> None:
    result = runner.invoke(app, ["search"])
    assert result.exit_code != 0


def test_search_renders_json_with_mocked_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub the search service and assert the CLI shape — no network."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.models.publication import Author
    from quelle.models.search import MergedHit

    fake = MergedHit(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani")],
        year=2017,
        type="article",
        doi="10.48550/arxiv.1706.03762",
        arxiv_id="1706.03762",
        sources=["openalex", "arxiv"],
        source_ids={"openalex": "https://openalex.org/W7"},
        score=0.0488,
    )
    monkeypatch.setattr(cli_main.search_service, "search", lambda *args, **kwargs: [fake])

    result = runner.invoke(app, ["--json", "search", "attention"])
    assert result.exit_code == 0
    assert "Attention Is All You Need" in result.output
    assert '"id": "doi:10.48550/arxiv.1706.03762"' in result.output
    assert '"id_resolvable": true' in result.output


def test_search_rejects_both_book_and_article(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--book` and `--article` are mutually exclusive — both set is a user error."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["search", "x", "--book", "--article"])
    assert result.exit_code == 1


def test_split_author_from_query_basic() -> None:
    from quelle.cli._helpers import split_author_from_query

    assert split_author_from_query("etranger, camus") == ("etranger", "camus")
    assert split_author_from_query("attention is all you need, vaswani") == (
        "attention is all you need",
        "vaswani",
    )


def test_split_author_from_query_no_comma() -> None:
    from quelle.cli._helpers import split_author_from_query

    assert split_author_from_query("attention is all you need") == (
        "attention is all you need",
        None,
    )


def test_split_author_from_query_rejects_year() -> None:
    """A trailing token containing digits is not a name."""
    from quelle.cli._helpers import split_author_from_query

    assert split_author_from_query("foo, 2024") == ("foo, 2024", None)


def test_split_author_from_query_rejects_too_many_tokens() -> None:
    """Trailing piece with >3 tokens is unlikely to be a single name; keep as-is."""
    from quelle.cli._helpers import split_author_from_query

    assert split_author_from_query("foo, alpha beta gamma delta") == (
        "foo, alpha beta gamma delta",
        None,
    )


def test_search_passes_split_author_to_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`search "etranger, camus"` should call the service with author=camus."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main

    captured: dict[str, object] = {}

    def fake_search(client, settings, query, **kwargs):
        captured["query"] = query
        captured["author"] = kwargs.get("author")
        return []

    monkeypatch.setattr(cli_main.search_service, "search", fake_search)
    result = runner.invoke(app, ["--json", "search", "etranger, camus"])
    assert result.exit_code == 0
    assert captured["query"] == "etranger"
    assert captured["author"] == "camus"


def test_search_no_comma_query_passes_through_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query without a comma must reach the service intact, with author=None."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main

    captured: dict[str, object] = {}

    def fake_search(client, settings, query, **kwargs):
        captured["query"] = query
        captured["author"] = kwargs.get("author")
        captured["type"] = kwargs.get("type")
        return []

    monkeypatch.setattr(cli_main.search_service, "search", fake_search)
    result = runner.invoke(app, ["--json", "search", "attention is all you need", "--book"])
    assert result.exit_code == 0
    assert captured["query"] == "attention is all you need"
    assert captured["author"] is None
    assert captured["type"] == "book"


def test_fetch_book_flag_threads_through_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`quelle fetch <title> --book` passes type_hint='book' through to the resolver."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.models.publication import Publication

    captured: dict[str, object] = {}

    def fake_resolve(client, settings, query, **kwargs):
        captured["query"] = query
        captured["type_hint"] = kwargs.get("type_hint")
        captured["author"] = kwargs.get("author")
        return Publication(title="Cannibal Capitalism", kind="book")

    monkeypatch.setattr(cli_main, "resolve_with_enrichment", fake_resolve)
    result = runner.invoke(app, ["--json", "fetch", "cannibal capitalism", "--book", "--no-cache"])
    assert result.exit_code == 0
    assert captured["query"] == "cannibal capitalism"
    assert captured["type_hint"] == "book"
    assert captured["author"] is None


def test_fetch_comma_query_splits_to_author(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`quelle fetch "title, surname" --book` splits and passes author through."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.models.publication import Publication

    captured: dict[str, object] = {}

    def fake_resolve(client, settings, query, **kwargs):
        captured["query"] = query
        captured["author"] = kwargs.get("author")
        return Publication(title="x", kind="book")

    monkeypatch.setattr(cli_main, "resolve_with_enrichment", fake_resolve)
    result = runner.invoke(
        app,
        ["--json", "fetch", "cannibal capitalism, fraser", "--book", "--no-cache"],
    )
    assert result.exit_code == 0
    assert captured["query"] == "cannibal capitalism"
    assert captured["author"] == "fraser"


def test_fetch_explicit_id_query_skips_comma_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DOI-shaped query with a comma keeps its full form, no author split."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.models.publication import Publication

    captured: dict[str, object] = {}

    def fake_resolve(client, settings, query, **kwargs):
        captured["query"] = query
        captured["author"] = kwargs.get("author")
        return Publication(title="x")

    monkeypatch.setattr(cli_main, "resolve_with_enrichment", fake_resolve)
    result = runner.invoke(app, ["--json", "fetch", "10.1234/foo,bar", "--no-cache"])
    assert result.exit_code == 0
    assert captured["query"] == "10.1234/foo,bar"
    assert captured["author"] is None


def test_fetch_rejects_both_book_and_article(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--book` and `--article` are mutually exclusive on fetch as well."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["fetch", "x", "--book", "--article"])
    assert result.exit_code == 1
