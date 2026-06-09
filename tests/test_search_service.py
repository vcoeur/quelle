"""Tests for the search orchestrator: source selection, RRF merging,
dedup by DOI/ISBN/arXiv id, tie-breaks, and graceful degradation when
a single source raises."""

from __future__ import annotations

from typing import Any

import pytest

from quelle.models.publication import Author
from quelle.models.search import SearchHit
from quelle.repositories.errors import NetworkError, UserError
from quelle.services import search as search_service


def _hit(
    *,
    source: str,
    rank: int,
    title: str,
    doi: str | None = None,
    isbn_13: str | None = None,
    arxiv_id: str | None = None,
    year: int | None = None,
    type_: str = "article",
    author: str = "Anon",
) -> SearchHit:
    return SearchHit(
        title=title,
        authors=[Author(name=author)],
        year=year,
        type=type_,  # type: ignore[arg-type]
        doi=doi,
        isbn_13=isbn_13,
        arxiv_id=arxiv_id,
        source=source,
        source_id=f"{source}:{title}",
        raw_rank=rank,
    )


def _patch_sources(monkeypatch: pytest.MonkeyPatch, fakes: dict[str, list[SearchHit]]) -> None:
    """Replace SOURCES with simple lambdas returning the given lists."""
    new_sources: dict[str, Any] = {}
    for name, hits in fakes.items():
        original = search_service.SOURCES.get(name)
        covers = original[1] if original else {"article", "book"}
        cap = original[2] if original else 200
        new_sources[name] = (lambda *_args, _hits=hits, **_kwargs: list(_hits), covers, cap)
    monkeypatch.setattr(search_service, "SOURCES", new_sources)


def test_dedup_by_doi_collapses_two_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sources(
        monkeypatch,
        {
            "openalex": [_hit(source="openalex", rank=0, title="Same paper", doi="10.1/x")],
            "semantic_scholar": [
                _hit(source="semantic_scholar", rank=2, title="Same paper", doi="10.1/x")
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert len(result) == 1
    assert set(result[0].sources) == {"openalex", "semantic_scholar"}


def test_rrf_score_rewards_top_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hit A appears at rank 0 in both sources.
    # Hit B appears at rank 5 in one source.
    _patch_sources(
        monkeypatch,
        {
            "openalex": [
                _hit(source="openalex", rank=0, title="A", doi="10.1/a"),
                _hit(source="openalex", rank=5, title="B", doi="10.1/b"),
            ],
            "semantic_scholar": [
                _hit(source="semantic_scholar", rank=0, title="A", doi="10.1/a"),
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert [hit.title for hit in result] == ["A", "B"]
    assert result[0].score > result[1].score


def test_graceful_degradation_when_one_source_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> list[SearchHit]:
        raise NetworkError("upstream timeout")

    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "openalex": (boom, {"article"}, 200),
            "semantic_scholar": (
                lambda *_a, **_k: [_hit(source="semantic_scholar", rank=0, title="Survivor")],
                {"article"},
                100,
            ),
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert [hit.title for hit in result] == ["Survivor"]


def test_type_filter_drops_book_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "openalex": (
                lambda *_a, **_k: [_hit(source="openalex", rank=0, title="Article", doi="10.1/a")],
                {"article", "book"},
                200,
            ),
            "open_library": (
                lambda *_a, **_k: [
                    _hit(source="open_library", rank=0, title="Book", isbn_13="978000")
                ],
                {"book"},
                100,
            ),
        },
    )
    result = search_service.search(client=None, settings=None, query="x", type="article")  # type: ignore[arg-type]
    titles = [hit.title for hit in result]
    assert "Book" not in titles


def test_unknown_source_filter_raises_user_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sources(monkeypatch, {"openalex": []})
    with pytest.raises(UserError):
        search_service.search(  # type: ignore[arg-type]
            client=None, settings=None, query="x", sources=["openlex"]
        )


def test_limit_below_one_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sources(monkeypatch, {"openalex": []})
    with pytest.raises(UserError):
        search_service.search(client=None, settings=None, query="x", limit=0)  # type: ignore[arg-type]


def test_tie_break_prefers_more_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two hits at rank 0 in their own source, but one is corroborated by a second.
    _patch_sources(
        monkeypatch,
        {
            "openalex": [
                _hit(source="openalex", rank=0, title="Solo", doi="10.1/solo"),
                _hit(source="openalex", rank=1, title="Both", doi="10.1/both"),
            ],
            "semantic_scholar": [
                _hit(source="semantic_scholar", rank=1, title="Both", doi="10.1/both"),
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    # 'Both' is found at rank 1 in two sources -> 2/(60+1) = 0.0328
    # 'Solo' is found at rank 0 in one -> 1/60 = 0.01666
    assert result[0].title == "Both"
    assert "openalex" in result[0].sources and "semantic_scholar" in result[0].sources


def test_sources_allowlist_narrows_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "openalex": (
                lambda *_a, **_k: [_hit(source="openalex", rank=0, title="A", doi="10.1/a")],
                {"article"},
                200,
            ),
            "semantic_scholar": (
                lambda *_a, **_k: [
                    _hit(source="semantic_scholar", rank=0, title="B", doi="10.1/b")
                ],
                {"article"},
                100,
            ),
        },
    )
    result = search_service.search(  # type: ignore[arg-type]
        client=None, settings=None, query="x", sources=["openalex"]
    )
    assert {hit.title for hit in result} == {"A"}


def test_fuzzy_merge_collapses_same_title_and_surname(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two hits with no shared id but matching title + first-author surname merge."""
    _patch_sources(
        monkeypatch,
        {
            "open_library": [
                _hit(
                    source="open_library",
                    rank=0,
                    title="L'Étranger",
                    type_="book",
                    author="Albert Camus",
                )
            ],
            "bnf": [
                _hit(
                    source="bnf",
                    rank=0,
                    title="L'Etranger",
                    type_="book",
                    author="Camus, Albert",
                )
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert len(result) == 1
    assert set(result[0].sources) == {"open_library", "bnf"}


def test_fuzzy_merge_keeps_translations_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different titles by the same author do NOT collapse — translation case."""
    _patch_sources(
        monkeypatch,
        {
            "open_library": [
                _hit(
                    source="open_library",
                    rank=0,
                    title="L'Étranger",
                    type_="book",
                    author="Albert Camus",
                )
            ],
            "google_books": [
                _hit(
                    source="google_books",
                    rank=0,
                    title="The Stranger",
                    type_="book",
                    author="Albert Camus",
                )
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert {hit.title for hit in result} == {"L'Étranger", "The Stranger"}


def test_fuzzy_merge_skips_when_no_authors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorless hits with the same title should NOT collapse — too risky."""

    def _no_author(source: str, title: str) -> SearchHit:
        return SearchHit(
            title=title,
            authors=[],
            type="book",
            source=source,
            source_id=f"{source}:{title}",
            raw_rank=0,
        )

    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "open_library": (
                lambda *_a, **_k: [_no_author("open_library", "Foo")],
                {"book"},
                100,
            ),
            "google_books": (
                lambda *_a, **_k: [_no_author("google_books", "Foo")],
                {"book"},
                40,
            ),
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert len(result) == 2


def test_type_book_drops_article_hits_from_multi_type_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAlex covers both kinds — when --book is set, article hits must be filtered out."""
    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "openalex": (
                lambda *_a, **_k: [
                    _hit(
                        source="openalex",
                        rank=0,
                        title="A review of Cannibal Capitalism",
                        type_="article",
                        doi="10.1/review",
                        author="Brian Milstein",
                    ),
                    _hit(
                        source="openalex",
                        rank=1,
                        title="Cannibal Capitalism",
                        type_="book",
                        isbn_13="9781839761232",
                        author="Nancy Fraser",
                    ),
                ],
                {"article", "book"},
                200,
            ),
        },
    )
    result = search_service.search(client=None, settings=None, query="x", type="book")  # type: ignore[arg-type]
    titles = {h.title for h in result}
    assert "A review of Cannibal Capitalism" not in titles
    assert "Cannibal Capitalism" in titles
    assert all(h.type in {"book", "unknown"} for h in result)


def test_type_book_keeps_unknown_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """An `unknown`-typed hit (type disagreement at merge time) is not dropped."""
    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {
            "openalex": (
                lambda *_a, **_k: [
                    _hit(source="openalex", rank=0, title="Maybe", type_="unknown"),
                ],
                {"article", "book"},
                200,
            ),
        },
    )
    result = search_service.search(client=None, settings=None, query="x", type="book")  # type: ignore[arg-type]
    assert [h.title for h in result] == ["Maybe"]


def test_kind_hint_threaded_to_openalex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service should pass `kind` to OpenAlex so it can add API-side type filtering."""
    captured: dict[str, object] = {}

    def fake_openalex(client, settings, query, *, author=None, kind=None, limit=20):
        captured["kind"] = kind
        captured["author"] = author
        return []

    monkeypatch.setattr(
        search_service,
        "SOURCES",
        {"openalex": (fake_openalex, {"article", "book"}, 200)},
    )
    search_service.search(client=None, settings=None, query="x", type="book")  # type: ignore[arg-type]
    assert captured["kind"] == "book"


def test_default_limit_returns_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service default `limit` is 3 (matches the CLI default)."""
    _patch_sources(
        monkeypatch,
        {
            "openalex": [
                _hit(source="openalex", rank=i, title=f"T{i}", doi=f"10.1/{i}") for i in range(8)
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert len(result) == 3


def test_merge_bridging_hit_unifies_doi_and_isbn_entries() -> None:
    """Regression: a hit sharing a DOI with entry A and an ISBN with entry B
    used to leave B behind — the same work appeared twice in the results."""
    a = _hit(source="openalex", rank=0, title="Work (OpenAlex)", doi="10.1/w", type_="book")
    b = _hit(
        source="open_library",
        rank=0,
        title="Work (Open Library)",
        isbn_13="9780140186338",
        type_="book",
    )
    bridge = _hit(
        source="google_books",
        rank=0,
        title="Work",
        doi="10.1/w",
        isbn_13="9780140186338",
        type_="book",
    )
    merged = search_service._merge([[a], [b], [bridge]])
    assert len(merged) == 1
    assert merged[0].doi == "10.1/w"
    assert merged[0].isbn_13 == "9780140186338"
    assert set(merged[0].sources) == {"openalex", "open_library", "google_books"}


def test_merge_after_bridge_later_hits_still_find_the_survivor() -> None:
    """Ids that pointed at the absorbed entry are re-pointed: a later hit
    matching only the absorbed entry's arXiv id merges into the survivor."""
    a = _hit(source="openalex", rank=0, title="Work", doi="10.1/w")
    b = _hit(source="semantic_scholar", rank=0, title="Work", arxiv_id="1706.03762")
    bridge = _hit(source="arxiv", rank=0, title="Work", doi="10.1/w", arxiv_id="1706.03762")
    late = _hit(source="arxiv", rank=1, title="Work", arxiv_id="1706.03762")
    merged = search_service._merge([[a], [b], [bridge], [late]])
    assert len(merged) == 1
    assert merged[0].doi == "10.1/w"
    assert merged[0].arxiv_id == "1706.03762"


def test_search_bridged_entries_do_not_duplicate_in_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the bridged work surfaces once regardless of the
    nondeterministic source completion order."""
    _patch_sources(
        monkeypatch,
        {
            "openalex": [
                _hit(
                    source="openalex",
                    rank=0,
                    title="Cannibal Capitalism",
                    doi="10.1/cc",
                    type_="book",
                    author="Nancy Fraser",
                )
            ],
            "open_library": [
                _hit(
                    source="open_library",
                    rank=0,
                    title="Cannibal capitalism (UK ed.)",
                    isbn_13="9781839761232",
                    type_="book",
                    author="Fraser, N.",
                )
            ],
            "google_books": [
                _hit(
                    source="google_books",
                    rank=0,
                    title="Cannibal Capitalism",
                    doi="10.1/cc",
                    isbn_13="9781839761232",
                    type_="book",
                    author="Nancy Fraser",
                )
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x", limit=10)  # type: ignore[arg-type]
    isbns = [hit.isbn_13 for hit in result if hit.isbn_13]
    assert isbns.count("9781839761232") == 1
    assert len(result) == 1


def test_preferred_id_falls_through_to_other_sources_ids() -> None:
    """`sources[0]` without a `source_ids` entry must not hide later ids."""
    from quelle.models.search import MergedHit

    hit = MergedHit(
        title="Work",
        sources=["bnf", "open_library"],
        source_ids={"open_library": "/works/OL123W"},
    )
    assert hit.preferred_id() == ("open_library", "/works/OL123W")


def test_dedup_by_isbn_collapses_book_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sources(
        monkeypatch,
        {
            "open_library": [
                _hit(
                    source="open_library",
                    rank=0,
                    title="Le Stranger",
                    isbn_13="9782070360024",
                    type_="book",
                )
            ],
            "google_books": [
                _hit(
                    source="google_books",
                    rank=1,
                    title="L'Étranger",
                    isbn_13="9782070360024",
                    type_="book",
                )
            ],
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0].isbn_13 == "9782070360024"
    assert set(result[0].sources) == {"open_library", "google_books"}
