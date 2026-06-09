"""Tests for Publication.merged_with — the enrichment merge rule."""

from __future__ import annotations

import re

from quelle.models.publication import Author, Publication


def _openalex_skeleton() -> Publication:
    return Publication(
        title="Active contours without edges",
        authors=[Author(name="Tony F. Chan"), Author(name="Luminita A. Vese")],
        year=2001,
        doi="10.1109/83.902291",
        openalex_id="https://openalex.org/W123",
        abstract=None,  # gap: no abstract
        venue=None,  # gap: no venue
        resolved_from_chain=["openalex"],
    )


def _crossref_full() -> Publication:
    return Publication(
        title="Active contours without edges",
        authors=[Author(name="Tony F. Chan"), Author(name="Luminita A. Vese")],
        year=2001,
        venue="IEEE Transactions on Image Processing",
        publisher="IEEE",
        doi="10.1109/83.902291",
        abstract="In this paper, we propose a new model...",
        citation_count=15000,
        journal_volume="10",
        journal_issue="2",
        page_range="266-277",
        resolved_from_chain=["crossref"],
    )


def test_merge_fills_missing_abstract_and_venue() -> None:
    merged = _openalex_skeleton().merged_with(_crossref_full())

    assert merged.abstract == "In this paper, we propose a new model..."
    assert merged.venue == "IEEE Transactions on Image Processing"
    assert merged.journal_volume == "10"
    assert merged.page_range == "266-277"
    assert merged.citation_count == 15000
    assert merged.resolved_from_chain == ["openalex", "crossref"]


def test_merge_never_overwrites_non_empty_fields() -> None:
    primary = _openalex_skeleton()
    # Inject an abstract into the primary, then try to merge a different one.
    primary_with_abstract = primary.merged_with(
        Publication(title="", abstract="original abstract", resolved_from_chain=["seed"])
    )
    merged = primary_with_abstract.merged_with(
        Publication(title="", abstract="replacement abstract", resolved_from_chain=["crossref"])
    )
    assert merged.abstract == "original abstract"
    # Both sources show up in the chain.
    assert merged.resolved_from_chain == ["openalex", "seed", "crossref"]


def test_merge_deduplicates_chain_entries() -> None:
    a = Publication(title="x", resolved_from_chain=["openalex"])
    b = Publication(title="x", resolved_from_chain=["openalex"])
    assert a.merged_with(b).resolved_from_chain == ["openalex"]


def test_merge_keeps_primary_title_even_if_empty_string() -> None:
    primary = Publication(title="", resolved_from_chain=["openalex"])
    other = Publication(title="Filled in", resolved_from_chain=["crossref"])
    assert primary.merged_with(other).title == "Filled in"


def test_citation_key_single_author() -> None:
    pub = Publication(
        title="The Perceptron",
        authors=[Author(name="Frank Rosenblatt")],
        year=1958,
    )
    assert pub.citation_key() == "Rosenblatt1958"


def test_citation_key_three_or_more_authors() -> None:
    pub = Publication(
        title="Geodesic Active Contours",
        authors=[
            Author(name="Vicent Caselles"),
            Author(name="Ron Kimmel"),
            Author(name="Guillermo Sapiro"),
        ],
        year=1997,
    )
    assert pub.citation_key() == "CasellesAl1997"


def test_citation_key_missing_author_and_year() -> None:
    pub = Publication(title="Anonymous")
    assert pub.citation_key() == "UnknownND"


def test_citation_key_whitespace_only_author_falls_back_to_unknown() -> None:
    # Regression: " ".split()[-1] used to raise IndexError and kill the run.
    pub = Publication(title="Garbage upstream", authors=[Author(name="   ")], year=2020)
    assert pub.citation_key() == "Unknown2020"


def test_citation_key_skips_unusable_author_names() -> None:
    pub = Publication(
        title="Partially garbage",
        authors=[Author(name=" "), Author(name="Frank Rosenblatt")],
        year=1958,
    )
    assert pub.citation_key() == "Rosenblatt1958"


def test_citation_key_sanitises_path_traversal_author_names() -> None:
    # Regression: upstream author names flowed into PDF paths unsanitised.
    pub = Publication(
        title="Evil",
        authors=[Author(name="../../../etc/passwd"), Author(name="Bob /tmp/x")],
        year=2021,
    )
    key = pub.citation_key()
    assert re.fullmatch(r"[A-Za-z0-9]+", key)
    assert key == "etcpasswdtmpx2021"


def test_citation_key_ascii_folds_accented_names() -> None:
    pub = Publication(title="Oratio", authors=[Author(name="François Récanati")], year=2020)
    assert pub.citation_key() == "Recanati2020"


def test_citation_key_keeps_surname_particles() -> None:
    pub = Publication(
        title="Cours de linguistique générale",
        authors=[Author(name="Ferdinand de Saussure")],
        year=1916,
    )
    assert pub.citation_key() == "Saussure1916"
