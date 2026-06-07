"""Normalised publication model.

All external sources (OpenAlex, Crossref, Semantic Scholar, arXiv,
Open Library, Google Books, BnF) map their raw responses into
`Publication` before returning. Downstream code (CLI output, cache,
JSON export) only ever sees this shape.

The same dataclass holds both articles and books — `kind` tags the
record and book-specific fields (`isbn_10`, `isbn_13`, `edition`,
`page_count`) sit alongside article-specific ones. None-valued fields
are omitted from rendering rather than gating logic on `kind`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any, Literal

Kind = Literal["article", "preprint", "book", "book-chapter", "web", "media"]

# Fields handled out-of-band by `Publication.merged_with`; everything else
# is folded automatically by the field-list-driven loop. Keeping the skip
# set tiny means new fields enrich for free without anyone remembering to
# touch the merge function.
#
# Merge contract for the auto-folded fields:
#
# 1. **Sticky identifiers.** `doi`, `arxiv_id`, `openalex_id`,
#    `semantic_scholar_id`, `isbn_10`, `isbn_13` are first-source-wins:
#    once a non-empty value is set, no later source overwrites it. This
#    is intentional — an OpenAlex DOI is taken as authoritative even if
#    a later Crossref payload disagrees. The flip-side is that a
#    misnormalised id from an earlier source sticks for the rest of the
#    chain; sources are responsible for emitting normalised ids.
# 2. **Opportunistic fills.** Every other scalar / list / dict field
#    fills only when the base record has no value (`None`, `""`, `[]`,
#    `{}`). False / 0 are real values and never trigger fills.
# 3. **`kind` precedence ladder.** `kind` is opportunistically filled
#    when missing, but if both base and other set it, `book-chapter`
#    wins over `article` (chapter-with-DOI case where OpenAlex
#    misclassifies); `book` likewise wins over `article` when both are
#    set. `preprint` and `article` tie — first-wins.
# 4. **`resolved_from_chain` is appended**, deduplicated, preserving
#    order — handled out of band.
_MERGE_SKIP_FIELDS: frozenset[str] = frozenset({"resolved_from_chain", "kind"})

# Precedence ladder used when both base and other set `kind`.
# Higher rank wins. Equal ranks keep the base value.
_KIND_PRIORITY: dict[str, int] = {
    "book-chapter": 3,
    "book": 2,
    "article": 1,
    "preprint": 1,
}


def _is_missing(value: Any) -> bool:
    """True when a field value should be treated as "fill me from `other`".

    None, empty string, empty list/dict/tuple all qualify. False, 0, and
    other meaningful zero-valued scalars do not — they are real values
    set by upstream sources and must not be overwritten.
    """
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple)):
        return not value
    return False


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
    isbn_10: str | None = None
    isbn_13: str | None = None
    edition: str | None = None
    page_count: int | None = None
    kind: Kind | None = None
    subjects: list[str] = field(default_factory=list)
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

        Falls back to `Unknown` / `ND` for missing author / year.
        """
        year = str(self.year) if self.year else "ND"
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

        Driven by `dataclasses.fields(Publication)` minus `_MERGE_SKIP_FIELDS`,
        so any new field added to the dataclass enriches automatically.
        """
        updates: dict[str, object] = {}
        for f in fields(self):
            if f.name in _MERGE_SKIP_FIELDS:
                continue
            my_value = getattr(self, f.name)
            if not _is_missing(my_value):
                continue
            other_value = getattr(other, f.name)
            if _is_missing(other_value):
                continue
            if isinstance(other_value, list):
                updates[f.name] = list(other_value)
            elif isinstance(other_value, dict):
                updates[f.name] = dict(other_value)
            else:
                updates[f.name] = other_value

        merged_chain = list(self.resolved_from_chain)
        for tag in other.resolved_from_chain:
            if tag not in merged_chain:
                merged_chain.append(tag)
        updates["resolved_from_chain"] = merged_chain

        merged_kind = _merge_kind(self.kind, other.kind)
        if merged_kind != self.kind:
            updates["kind"] = merged_kind

        return replace(self, **updates)


def _merge_kind(base: Kind | None, other: Kind | None) -> Kind | None:
    """Resolve `kind` per the precedence ladder.

    Missing values are filled from the other side. When both are set,
    `_KIND_PRIORITY` decides which wins. Equal ranks keep the base.
    """
    if base is None:
        return other
    if other is None:
        return base
    base_rank = _KIND_PRIORITY.get(base, 0)
    other_rank = _KIND_PRIORITY.get(other, 0)
    return other if other_rank > base_rank else base
