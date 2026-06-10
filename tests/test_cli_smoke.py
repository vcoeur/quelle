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
    """Bare `quelle` prints the usage view and exits with the usage code."""
    result = runner.invoke(app, [])
    assert result.exit_code == 64
    assert "Usage" in result.output


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


# --- exit-code contract -----------------------------------------------------


def test_usage_error_missing_argument_exits_64() -> None:
    """Click usage errors must not collide with the documented network code 2."""
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 64


def test_usage_error_unknown_option_exits_64() -> None:
    result = runner.invoke(app, ["fetch", "x", "--no-such-flag"])
    assert result.exit_code == 64


def test_corrupt_cache_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`cache list` on a corrupt SQLite file reports CacheError and exits 3."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "cache.sqlite").write_bytes(b"this is not a sqlite database " * 20)
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    result = runner.invoke(app, ["cache", "list"])
    assert result.exit_code == 3


def test_malformed_env_value_exits_4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-numeric QUELLE_HTTP_TIMEOUT is a ConfigError (4), not a traceback."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_HTTP_TIMEOUT", "not-a-number")

    result = runner.invoke(app, ["cache", "list"])
    assert result.exit_code == 4
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_fetch_not_found_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolver miss is the documented exit 1, not a traceback."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.repositories.errors import NotFoundError

    def raise_not_found(*args, **kwargs):
        raise NotFoundError("no record for query")

    monkeypatch.setattr(cli_main, "resolve_with_enrichment", raise_not_found)
    result = runner.invoke(app, ["fetch", "10.1234/nope", "--no-cache"])
    assert result.exit_code == 1
    assert "NotFoundError" in result.output


def test_fetch_network_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upstream failure is the documented exit 2 (network), not 1."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.repositories.errors import NetworkError

    def raise_network_error(*args, **kwargs):
        raise NetworkError("503 from upstream", status_code=503)

    monkeypatch.setattr(cli_main, "resolve_with_enrichment", raise_network_error)
    result = runner.invoke(app, ["fetch", "10.1234/flaky", "--no-cache"])
    assert result.exit_code == 2
    assert "NetworkError" in result.output


def test_resolve_network_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`resolve` honours the same exit-code contract as `fetch`."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.repositories.errors import NetworkError

    def raise_network_error(*args, **kwargs):
        raise NetworkError("timeout", status_code=None)

    monkeypatch.setattr(cli_main, "resolve_any", raise_network_error)
    result = runner.invoke(app, ["resolve", "10.1234/flaky"])
    assert result.exit_code == 2


def test_search_network_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`search` honours the same exit-code contract as `fetch`."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.cli import main as cli_main
    from quelle.repositories.errors import NetworkError

    def raise_network_error(*args, **kwargs):
        raise NetworkError("upstream 502", status_code=502)

    monkeypatch.setattr(cli_main.search_service, "search", raise_network_error)
    result = runner.invoke(app, ["search", "anything"])
    assert result.exit_code == 2


def test_exit_code_for_maps_every_documented_code() -> None:
    """The classifier itself honours the documented exit-code table."""
    from quelle.cli._helpers import exit_code_for
    from quelle.repositories.errors import (
        CacheError,
        ConfigError,
        NetworkError,
        NotFoundError,
        PublicationsError,
        RateLimitError,
        UserError,
    )

    assert exit_code_for(UserError("x")) == 1
    assert exit_code_for(NotFoundError("x")) == 1
    assert exit_code_for(NetworkError("x")) == 2
    assert exit_code_for(RateLimitError("x", status_code=429)) == 2  # subclass of NetworkError
    assert exit_code_for(CacheError("x")) == 3
    assert exit_code_for(ConfigError("x")) == 4
    # Unclassified expected errors degrade to the generic user code.
    assert exit_code_for(PublicationsError("x")) == 1


def test_report_error_hints_cover_subclasses(capsys) -> None:
    """A RateLimitError gets the NetworkError hint via the isinstance walk."""
    from quelle.cli._helpers import report_error
    from quelle.repositories.errors import RateLimitError

    report_error(RateLimitError("slow down", status_code=429))
    captured = capsys.readouterr()
    assert "RateLimitError" in captured.err
    assert "Network or upstream API failure" in captured.err


# --- User-Agent + polite-pool warning ---------------------------------------


def test_user_agent_carries_package_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quelle import __version__
    from quelle.settings import load_settings

    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("QUELLE_USER_AGENT", raising=False)
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    settings = load_settings()
    assert settings.user_agent == f"quelle/{__version__} (+mailto:alice@example.com)"


def test_build_client_warns_without_contact_email(tmp_settings, capsys) -> None:
    from dataclasses import replace as dc_replace

    from quelle.repositories.http_client import build_client

    anonymous = dc_replace(tmp_settings, contact_email="")
    with build_client(anonymous):
        pass
    captured = capsys.readouterr()
    assert captured.out == ""  # never stdout — --json output stays clean
    assert "polite-pool" in captured.err
    assert "quelle config edit" in captured.err


def test_build_client_silent_with_contact_email(tmp_settings, capsys) -> None:
    from quelle.repositories.http_client import build_client

    with build_client(tmp_settings):
        pass
    captured = capsys.readouterr()
    assert captured.err == ""


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
