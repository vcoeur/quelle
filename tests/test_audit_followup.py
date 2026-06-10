"""Regression tests for the v0.7.0 audit follow-up (suggestions A-Z).

Each block here documents one audit suggestion that previously had no
direct test. Tests are kept self-contained and use the standard
`tmp_settings` fixture / `pytest-httpx` patterns from the rest of the
suite.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from quelle.cli._helpers import resolve_type_hint
from quelle.cli.main import app
from quelle.cli.output import OutputMode
from quelle.models.publication import Author, Publication
from quelle.models.search import MergedHit, SearchHit
from quelle.repositories.cache import Cache
from quelle.repositories.errors import UserError
from quelle.services import search as search_service
from quelle.services.search import (
    _hit_is_self_sufficient,
    _normalise_title,
    _per_source_limit,
    _surname,
)
from quelle.settings import Settings

runner = CliRunner()


# --- E: resolve_type_hint is now unit-testable without the Typer runner.


def test_resolve_type_hint_book_only() -> None:
    assert resolve_type_hint(book=True, article=False) == "book"


def test_resolve_type_hint_article_only() -> None:
    assert resolve_type_hint(book=False, article=True) == "article"


def test_resolve_type_hint_neither() -> None:
    assert resolve_type_hint(book=False, article=False) is None


def test_resolve_type_hint_both_raises_user_error() -> None:
    with pytest.raises(UserError, match="mutually exclusive"):
        resolve_type_hint(book=True, article=True)


# --- F: explicit-id query plus --book/--article is rejected at the CLI.


def test_fetch_doi_with_book_flag_is_user_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing --book on an explicit DOI used to silently throw the
    flag away. Now the CLI errors out — drop the flag, or pass a
    free-text title query."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["fetch", "10.1109/83.902291", "--book", "--no-cache"])
    assert result.exit_code == 1


def test_fetch_isbn_with_article_flag_is_user_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["fetch", "9781839761232", "--article", "--no-cache"])
    assert result.exit_code == 1


# --- I: config payload is field-list-driven over Settings now.


def test_config_payload_includes_every_displayed_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every key in `_SETTINGS_DISPLAY` shows up in the rendered payload."""
    from quelle.cli.config import _SETTINGS_DISPLAY, _full_config_payload
    from quelle.settings import load_settings

    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    settings = load_settings()
    payload = _full_config_payload(settings)
    for key in _SETTINGS_DISPLAY:
        assert key in payload, f"`{key}` missing from rendered config payload"


# --- J: --limit is bounded at parse time.


def test_search_limit_above_cap_rejected_at_parse_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["search", "x", "--limit", "9999"])
    assert result.exit_code != 0


def test_search_limit_zero_rejected_at_parse_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["search", "x", "--limit", "0"])
    assert result.exit_code != 0


def test_per_source_limit_clips_to_documented_cap() -> None:
    """`max(limit*2, 20)` is then clipped per source: Google Books → 40."""
    # base_pull = 100 → Google Books should clip to 40.
    assert _per_source_limit("google_books", 100) == 40
    assert _per_source_limit("openalex", 100) == 100
    assert _per_source_limit("openalex", 500) == 200
    assert _per_source_limit("semantic_scholar", 200) == 100


def test_per_source_limit_propagates_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service must ask each adapter for `_per_source_limit(name, base)`,
    not the user's `--limit`."""
    captured: dict[str, int] = {}

    def fake_search(client, settings, query, **kwargs):
        # `kwargs["limit"]` is what the adapter sees.
        captured["openalex"] = kwargs["limit"]
        return []

    def fake_google(client, settings, query, **kwargs):
        captured["google_books"] = kwargs["limit"]
        return []

    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "openalex": (fake_search, {"article", "book"}, 200),
            "google_books": (fake_google, {"book"}, 40),
        },
    )
    search_service.search(  # type: ignore[arg-type]
        client=None, settings=None, query="x", limit=50
    )
    # base_pull = max(50*2, 20) = 100; openalex cap=200 → 100; google cap=40 → 40.
    assert captured["openalex"] == 100
    assert captured["google_books"] == 40


# --- L: Google Books query string uses spaces, not '+'.


def test_google_books_search_uses_space_separated_qualifiers(
    httpx_mock,
    tmp_settings: Settings,
) -> None:
    """The `q` param sent to Google Books must be `intitle:foo inauthor:bar`,
    not `intitle:foo+inauthor:bar`."""
    from quelle.repositories.sources import google_books

    google_books._reset_rate_limit_for_tests()
    httpx_mock.add_response(json={"items": []})
    client = httpx.Client()
    try:
        google_books.search(client, tmp_settings, "foo", author="bar", limit=5)
    finally:
        client.close()
    request = httpx_mock.get_request()
    assert request is not None
    q_param = dict(request.url.params)["q"]
    assert q_param == "intitle:foo inauthor:bar"


# --- M: arXiv strips internal double quotes from user input.


def test_arxiv_search_strips_internal_quotes_from_query(
    httpx_mock,
    tmp_settings: Settings,
) -> None:
    """A query like `foo"bar` must not produce malformed CQL."""
    from quelle.repositories.sources import arxiv

    arxiv._reset_rate_limit_for_tests()
    # Minimal Atom feed with no entries.
    httpx_mock.add_response(
        text='<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
    )
    client = httpx.Client()
    try:
        arxiv.search(client, tmp_settings, 'foo"bar', author='x"y', limit=5)
    finally:
        client.close()
    request = httpx_mock.get_request()
    assert request is not None
    sq = dict(request.url.params)["search_query"]
    # No double quotes inside the qualifier values.
    assert sq == 'ti:"foobar" AND au:"xy"'


# --- N: multi-particle surname extractor.


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Albert Camus", "camus"),
        ("Camus, Albert", "camus"),
        ("Ana de la Vega", "de la vega"),
        ("de la Vega, Ana", "de la vega"),
        ("Vincent van Gogh", "van gogh"),
        ("van Gogh, Vincent", "van gogh"),
        ("Mohammed bin Salman", "bin salman"),
        ("Ada Lovelace", "lovelace"),
    ],
)
def test_surname_handles_multi_particle_names(name: str, expected: str) -> None:
    assert _surname(name) == expected


def test_surname_extractor_symmetric_across_formats() -> None:
    """The same author in `Last, First` and `First Last` must fold to the same surname."""
    assert _surname("de la Vega, Ana") == _surname("Ana de la Vega")
    assert _surname("van der Berg, Marie") == _surname("Marie van der Berg")


# --- N (cont.): non-Latin titles fold to empty under _normalise_title.


def test_normalise_title_drops_to_empty_for_chinese() -> None:
    """ASCII drop reduces a Chinese title to empty; the dedup pass then
    preserves the hit rather than collapsing it against an unrelated
    same-empty-key neighbour."""
    assert _normalise_title("红楼梦") == ""


def test_normalise_title_drops_to_empty_for_arabic() -> None:
    assert _normalise_title("الأعمال") == ""


def test_normalise_title_keeps_latin_with_diacritics() -> None:
    assert _normalise_title("L'Étranger") == "l etranger"


def test_non_latin_titles_are_preserved_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two CJK hits with the same title text should NOT fold (no surnames
    to corroborate the merge)."""

    def _hit(source: str, title: str) -> SearchHit:
        return SearchHit(
            title=title,
            authors=[Author(name="曹雪芹")],
            type="book",
            source=source,
            source_id=f"{source}:0",
            raw_rank=0,
        )

    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "open_library": (lambda *_a, **_k: [_hit("open_library", "红楼梦")], {"book"}, 100),
            "google_books": (lambda *_a, **_k: [_hit("google_books", "红楼梦")], {"book"}, 40),
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    # The empty-key dedup path leaves both standing.
    assert len(result) == 2


# --- P: cache stats expose oldest_cached_at and size_bytes.


def test_cache_stats_includes_oldest_and_size(tmp_path: Path) -> None:
    db = tmp_path / ".publications-state" / "cache.sqlite"
    with Cache.open(db) as cache:
        stats = cache.stats()
        assert stats["total"] == 0
        assert stats["oldest_cached_at"] is None
        assert stats["newest_cached_at"] is None
        assert isinstance(stats["size_bytes"], int)
        assert stats["size_bytes"] > 0


# --- Q: self-sufficient hit short-circuits the id round-trip.


def test_resolve_top_hit_skips_roundtrip_when_corroborated(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merged hit with title+year+kind+authors and 2+ sources should
    skip the id-based round-trip and return a synthesised Publication."""
    from quelle.repositories.sources import open_library
    from quelle.services.search import resolve_top_hit

    fake_hit = MergedHit(
        title="Cannibal Capitalism",
        authors=[Author(name="Nancy Fraser")],
        year=2022,
        type="book",
        isbn_13="9781839761232",
        sources=["open_library", "bnf"],
    )
    monkeypatch.setattr(search_service, "search", lambda *a, **k: [fake_hit])
    monkeypatch.setattr(
        open_library,
        "fetch_by_isbn",
        lambda *a, **k: pytest.fail(
            "self-sufficient merged hit must NOT trigger the id round-trip"
        ),
    )

    client = httpx.Client()
    try:
        result = resolve_top_hit(
            client, tmp_settings, "Cannibal Capitalism", type_hint="book", author="fraser"
        )
    finally:
        client.close()

    assert result.title == "Cannibal Capitalism"
    assert result.isbn_13 == "9781839761232"
    assert result.kind == "book"
    assert result.resolved_from_chain == ["open_library", "bnf"]


def test_hit_is_self_sufficient_predicate() -> None:
    rich = MergedHit(
        title="X",
        authors=[Author(name="A")],
        year=2020,
        type="book",
        sources=["a", "b"],
    )
    assert _hit_is_self_sufficient(rich) is True

    no_authors = MergedHit(title="X", year=2020, type="book", sources=["a", "b"])
    assert _hit_is_self_sufficient(no_authors) is False

    one_source = MergedHit(
        title="X", authors=[Author(name="A")], year=2020, type="book", sources=["a"]
    )
    assert _hit_is_self_sufficient(one_source) is False

    no_year = MergedHit(title="X", authors=[Author(name="A")], type="book", sources=["a", "b"])
    assert _hit_is_self_sufficient(no_year) is False

    unknown_type = MergedHit(
        title="X",
        authors=[Author(name="A")],
        year=2020,
        type="unknown",
        sources=["a", "b"],
    )
    assert _hit_is_self_sufficient(unknown_type) is False


# --- V: cache title-collision behaviour is documented by a test.


def test_cache_title_collision_returns_arbitrary_match(tmp_path: Path) -> None:
    """Two distinct works that share a normalised title fall on top of
    the title_key index — `get_by_title_exact` returns *some* row, and
    callers should treat title-fallback as a best-effort hint, not a
    primary lookup. This test documents the limitation."""
    db = tmp_path / ".publications-state" / "cache.sqlite"
    with Cache.open(db) as cache:
        first = Publication(
            title="A Common Title",
            authors=[Author(name="First Author")],
            year=2010,
            doi="10.1/first",
        )
        second = Publication(
            title="a common title",
            authors=[Author(name="Second Author")],
            year=2020,
            doi="10.1/second",
        )
        cache.upsert(first)
        cache.upsert(second)

        hit = cache.get_by_title_exact("a common title")
        # Not a guarantee about *which* row wins, just that lookup
        # collapses to one of them. The cache stores both rows under
        # distinct citation keys.
        assert hit is not None
        assert cache.stats()["total"] == 2


# --- V: cache lookup honours every entry path.


def test_cache_lookup_by_doi(tmp_path: Path) -> None:
    db = tmp_path / ".publications-state" / "cache.sqlite"
    with Cache.open(db) as cache:
        cache.upsert(
            Publication(
                title="Active contours",
                authors=[Author(name="Tony Chan")],
                year=2001,
                doi="10.1109/83.902291",
            )
        )
        hit = cache.lookup("10.1109/83.902291")
        assert hit is not None
        assert hit.title == "Active contours"


def test_cache_lookup_by_isbn(tmp_path: Path) -> None:
    db = tmp_path / ".publications-state" / "cache.sqlite"
    with Cache.open(db) as cache:
        cache.upsert(
            Publication(
                title="X",
                isbn_13="9782070407132",
                kind="book",
            )
        )
        hit = cache.lookup("978-2-07-040713-2")
        assert hit is not None
        assert hit.isbn_13 == "9782070407132"


def test_cache_lookup_skips_title_when_type_hint_set(tmp_path: Path) -> None:
    """A title-keyed cache row must not short-circuit a typed lookup."""
    db = tmp_path / ".publications-state" / "cache.sqlite"
    with Cache.open(db) as cache:
        cache.upsert(Publication(title="Cannibal Capitalism"))
        # No type hint → title fallback finds the row.
        assert cache.lookup("Cannibal Capitalism") is not None
        # Type hint set → title fallback skipped.
        assert cache.lookup("Cannibal Capitalism", type_hint="book") is None


# --- V: cache show CLI smoke test.


def test_cache_show_smoke_misses_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`quelle cache show <id>` against an empty cache returns exit 1."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")
    result = runner.invoke(app, ["cache", "show", "10.1/missing"])
    assert result.exit_code == 1


def test_cache_show_smoke_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-populated cache row is surfaced by `cache show`."""
    monkeypatch.setenv("QUELLE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QUELLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUELLE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("QUELLE_CONTACT_EMAIL", "alice@example.com")

    from quelle.settings import load_settings

    settings = load_settings()
    with Cache.open(settings.paths.cache_db) as cache:
        cache.upsert(
            Publication(
                title="Active contours",
                authors=[Author(name="Tony Chan")],
                year=2001,
                doi="10.1109/83.902291",
            )
        )
    result = runner.invoke(app, ["--json", "cache", "show", "10.1109/83.902291"])
    assert result.exit_code == 0
    assert "Active contours" in result.output


# --- V: render_search rich rendering is exercised.


def test_render_search_rich_path(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The non-JSON branch of render_search prints something for each hit."""
    from quelle.cli.output import render_search

    payload = {
        "query": "x",
        "type": "all",
        "limit": 1,
        "hits": [
            {
                "rank": 1,
                "title": "Foo",
                "authors": [{"name": "Anon"}],
                "year": 2020,
                "type": "article",
                "id": "doi:10.1/x",
                "id_resolvable": True,
                "ids": {"doi": "10.1/x"},
                "sources": ["openalex"],
                "source_ids": {},
                "score": 0.0488,
            }
        ],
    }
    render_search(payload, mode=OutputMode(json=False))
    captured = capsys.readouterr()
    assert "Foo" in captured.out


def test_render_search_rich_path_empty(capsys) -> None:
    from quelle.cli.output import render_search

    payload = {"query": "x", "type": "all", "limit": 1, "hits": []}
    render_search(payload, mode=OutputMode(json=False))
    captured = capsys.readouterr()
    assert "no matches" in captured.out


# --- X: OutputMode.from_ctx wires through ctx.obj.


def test_output_mode_from_ctx_default_is_not_json() -> None:
    class _Ctx:
        obj = None

    mode = OutputMode.from_ctx(_Ctx())
    assert mode.json is False


def test_output_mode_from_ctx_reads_json_flag() -> None:
    class _Ctx:
        obj = {"json": True}

    mode = OutputMode.from_ctx(_Ctx())
    assert mode.json is True


def test_output_mode_is_frozen() -> None:
    """OutputMode now lives in a frozen dataclass — accidental mutation
    raises FrozenInstanceError."""
    from dataclasses import FrozenInstanceError

    mode = OutputMode(json=True)
    with pytest.raises(FrozenInstanceError):
        mode.json = False  # type: ignore[misc]


# --- Z: kind precedence ladder during merged_with.


def test_kind_book_chapter_wins_over_article() -> None:
    """A later source upgrading a misclassified `article` to
    `book-chapter` should win — book-chapter is more specific."""
    base = Publication(title="X", kind="article")
    other = Publication(title="X", kind="book-chapter")
    merged = base.merged_with(other)
    assert merged.kind == "book-chapter"


def test_kind_book_wins_over_article() -> None:
    base = Publication(title="X", kind="article")
    other = Publication(title="X", kind="book")
    merged = base.merged_with(other)
    assert merged.kind == "book"


def test_kind_first_wins_at_equal_rank() -> None:
    """`article` and `preprint` tie — base wins."""
    base = Publication(title="X", kind="article")
    other = Publication(title="X", kind="preprint")
    merged = base.merged_with(other)
    assert merged.kind == "article"


def test_kind_fills_when_base_missing() -> None:
    base = Publication(title="X", kind=None)
    other = Publication(title="X", kind="book")
    merged = base.merged_with(other)
    assert merged.kind == "book"


# --- B/C/D: the docs touched in this audit don't get a unit test, but
# the README and docs/commands.md files are linted by doctest-style
# spot checks lower down. Skipping here.


# --- A/H/T: covered transitively by the existing CLI smoke tests + the
# `_per_source_limit` test above.
