"""Normalised publication model.

All external sources (OpenAlex, Crossref, Semantic Scholar, arXiv)
map their raw responses into `Publication` before returning.
Downstream code (CLI output, cache, Kasten handoff) only ever sees
this shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Author:
    """A single author with optional ORCID and affiliation."""

    name: str
    orcid: str | None = None
    affiliation: str | None = None


@dataclass(frozen=True)
class Publication:
    """Normalised metadata for a single publication.

    All fields except `title` are optional. Missing values are
    represented as `None` (scalar) or `[]` (list) so downstream code
    can rely on attribute access without `AttributeError`.

    `resolved_from_chain` records which sources contributed to the
    final record, in the order they were consulted. A single-source
    resolution contains one entry (`["openalex"]`); an enrichment run
    that gets the abstract from Crossref after the metadata from
    OpenAlex contains `["openalex", "crossref"]`.
    """

    title: str
    authors: list[Author] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    abstract: str | None = None
    citation_count: int | None = None
    is_open_access: bool | None = None
    pdf_url: str | None = None
    local_pdf_path: str | None = None
    source_url: str | None = None
    topics: list[str] = field(default_factory=list)
    journal_volume: str | None = None
    journal_issue: str | None = None
    page_range: str | None = None
    resolved_from_chain: list[str] = field(default_factory=list)

    def resolved_from_chain_head(self) -> str:
        """Return the first source in the chain, or `"unknown"`."""
        return self.resolved_from_chain[0] if self.resolved_from_chain else "unknown"

    def citation_key(self) -> str:
        """Short BibTeX-style key.

        - Single author: `LastnameYear` (e.g. `Rosenblatt1958`)
        - Two authors: `Last1Last2Year` (e.g. `KahnemanTversky1972`)
        - Three or more: `LastnameAlYear` (e.g. `CasellesAl1997`)

        Falls back to `Unknown` / `nd` for missing author / year.
        """
        year = str(self.year) if self.year else "nd"
        if not self.authors or not self.authors[0].name:
            return f"Unknown{year}"

        def _last(name: str) -> str:
            return name.split()[-1].replace("-", "")

        if len(self.authors) == 1:
            return f"{_last(self.authors[0].name)}{year}"
        if len(self.authors) == 2:
            return f"{_last(self.authors[0].name)}{_last(self.authors[1].name)}{year}"
        return f"{_last(self.authors[0].name)}Al{year}"

    def merged_with(self, other: Publication) -> Publication:
        """Return a new Publication that fills `None` / `[]` gaps from `other`.

        Non-empty fields on `self` are never overwritten — this is a
        strictly additive merge, used to enrich an OpenAlex result
        with missing-field data from Crossref / Semantic Scholar /
        arXiv. The `resolved_from_chain` of `other` is appended to
        `self`'s chain, deduplicated, preserving order.
        """
        updates: dict[str, object] = {}
        for f in (
            "year",
            "venue",
            "publisher",
            "doi",
            "arxiv_id",
            "openalex_id",
            "semantic_scholar_id",
            "abstract",
            "citation_count",
            "is_open_access",
            "pdf_url",
            "local_pdf_path",
            "source_url",
            "journal_volume",
            "journal_issue",
            "page_range",
        ):
            if getattr(self, f) is None:
                other_value = getattr(other, f)
                if other_value is not None:
                    updates[f] = other_value
        if not self.title and other.title:
            updates["title"] = other.title
        if not self.authors and other.authors:
            updates["authors"] = list(other.authors)
        if not self.topics and other.topics:
            updates["topics"] = list(other.topics)

        merged_chain = list(self.resolved_from_chain)
        for tag in other.resolved_from_chain:
            if tag not in merged_chain:
                merged_chain.append(tag)
        updates["resolved_from_chain"] = merged_chain

        return replace(self, **updates)
