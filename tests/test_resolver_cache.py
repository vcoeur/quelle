"""Tests that the resolver short-circuits on cache hits."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from quelle.models.publication import Author, Publication
from quelle.repositories.cache import Cache
from quelle.services.resolver import resolve_with_enrichment


def _exploding_client() -> httpx.Client:
    """An httpx.Client that raises on every request — proves we didn't hit the network."""

    def _transport(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be touched on cache hit")

    transport = httpx.MockTransport(_transport)
    return httpx.Client(transport=transport)


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    with Cache.open(tmp_path / ".publications-state" / "cache.sqlite") as c:
        yield c


def test_cache_hit_by_doi_skips_network(cache: Cache, tmp_settings) -> None:
    cached = Publication(
        title="Active contours without edges",
        authors=[Author(name="Tony F. Chan")],
        year=2001,
        doi="10.1109/83.902291",
        resolved_from_chain=["openalex", "crossref"],
    )
    cache.upsert(cached)

    with _exploding_client() as client:
        result = resolve_with_enrichment(client, tmp_settings, "10.1109/83.902291", cache=cache)

    assert result.title == "Active contours without edges"
    assert result.resolved_from_chain == ["openalex", "crossref"]


def test_cache_hit_by_arxiv_id_skips_network(cache: Cache, tmp_settings) -> None:
    cached = Publication(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani")],
        year=2017,
        arxiv_id="1706.03762",
        resolved_from_chain=["arxiv"],
    )
    cache.upsert(cached)

    with _exploding_client() as client:
        result = resolve_with_enrichment(client, tmp_settings, "1706.03762", cache=cache)

    assert result.arxiv_id == "1706.03762"


def test_cache_hit_by_title_skips_network(cache: Cache, tmp_settings) -> None:
    cached = Publication(
        title="The Perceptron",
        authors=[Author(name="Frank Rosenblatt")],
        year=1958,
        resolved_from_chain=["openalex"],
    )
    cache.upsert(cached)

    with _exploding_client() as client:
        result = resolve_with_enrichment(client, tmp_settings, "The Perceptron", cache=cache)
    assert result.year == 1958


def test_cache_miss_would_hit_network(cache: Cache, tmp_settings) -> None:
    # The cache is empty; an exploding client raises.
    with _exploding_client() as client, pytest.raises(AssertionError):
        resolve_with_enrichment(client, tmp_settings, "10.nope/missing", cache=cache)
