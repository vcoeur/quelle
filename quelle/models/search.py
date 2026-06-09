"""Search-result model.

`SearchHit` represents a single candidate returned by one source's
search endpoint. `MergedHit` represents the result of fusing the same
underlying publication across multiple sources, with a Reciprocal Rank
Fusion score and the union of identifiers / source attributions.

Distinct from `Publication`: a `SearchHit` is intentionally lighter —
just enough to display a candidate list and feed the chosen id back to
`quelle fetch`. Full metadata (abstract, citation count, OA status) is
not fetched at search time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from quelle.models.publication import Author

HitType = Literal["book", "article", "unknown"]


@dataclass(frozen=True)
class SearchHit:
    """A single candidate returned by one source's search endpoint."""

    title: str
    authors: list[Author] = field(default_factory=list)
    year: int | None = None
    type: HitType = "unknown"
    doi: str | None = None
    isbn_13: str | None = None
    isbn_10: str | None = None
    arxiv_id: str | None = None
    source: str = ""
    source_id: str = ""
    raw_rank: int = 0


@dataclass(frozen=True)
class MergedHit:
    """A single publication after merging matching `SearchHit`s.

    `sources` is the list of adapter slugs that surfaced this hit (in
    the order they appeared during merging — first-seen wins).
    `score` is the Reciprocal Rank Fusion score; magnitudes are not
    human-meaningful and meant only for ranking.
    """

    title: str
    authors: list[Author] = field(default_factory=list)
    year: int | None = None
    type: HitType = "unknown"
    doi: str | None = None
    isbn_13: str | None = None
    isbn_10: str | None = None
    arxiv_id: str | None = None
    sources: list[str] = field(default_factory=list)
    source_ids: dict[str, str] = field(default_factory=dict)
    score: float = 0.0

    def preferred_id(self) -> tuple[str, str] | None:
        """Return the (kind, value) pair of the preferred id, if any.

        Resolution ladder: doi → isbn-13 → arxiv → first source-native id.
        The first three are values `quelle fetch` accepts; the fourth is
        a human reference only.
        """
        if self.doi:
            return ("doi", self.doi)
        if self.isbn_13:
            return ("isbn", self.isbn_13)
        if self.arxiv_id:
            return ("arxiv", self.arxiv_id)
        # Walk sources in first-seen order, then any id-bearing source not
        # listed in `sources` — the first source may not carry an id.
        for source in (*self.sources, *self.source_ids):
            value = self.source_ids.get(source)
            if value:
                return (source, value)
        return None
