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
        original_covers = search_service.SOURCES.get(name)
        covers = original_covers[1] if original_covers else {"article", "book"}
        new_sources[name] = (lambda *_args, _hits=hits, **_kwargs: list(_hits), covers)
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
            "openalex": (boom, {"article"}),
            "semantic_scholar": (
                lambda *_a, **_k: [_hit(source="semantic_scholar", rank=0, title="Survivor")],
                {"article"},
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
            ),
            "open_library": (
                lambda *_a, **_k: [
                    _hit(source="open_library", rank=0, title="Book", isbn_13="978000")
                ],
                {"book"},
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
            ),
            "semantic_scholar": (
                lambda *_a, **_k: [
                    _hit(source="semantic_scholar", rank=0, title="B", doi="10.1/b")
                ],
                {"article"},
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
            ),
            "google_books": (
                lambda *_a, **_k: [_no_author("google_books", "Foo")],
                {"book"},
            ),
        },
    )
    result = search_service.search(client=None, settings=None, query="x")  # type: ignore[arg-type]
    assert len(result) == 2


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
