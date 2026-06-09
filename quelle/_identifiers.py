"""Bare-identifier recognition — DOI, arXiv id, ISBN. Pure string logic, no I/O.

The single owner of identifier recognition: the resolver's query
routing and the cache's lookup keying both import the regexes and
extraction helpers from here, so the two can never drift apart (they
used to carry byte-for-byte copies). Only `quelle._isbn` is imported —
this module sits at the same low layer.

Scope is *bare* identifiers (a DOI string, an arXiv id, an ISBN in any
of its written forms). Extracting identifiers embedded in arbitrary
URLs is the resolver's routing concern and stays in
`services/resolver.py`, built on top of these primitives.
"""

from __future__ import annotations

import re

from quelle._isbn import isbn10_to_13, isbn13_to_10

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")

# New-style ids (`1706.03762`, optional `vN`) and old-style ids with an
# archive plus optional two-letter subject class (`math/0211159`,
# `math.GT/0309136`).
ARXIV_ID_RE = re.compile(
    r"^(\d{4}\.\d{4,5}(v\d+)?|[a-z\-]+(\.[a-z]{2})?/\d{7}(v\d+)?)$", re.IGNORECASE
)

# Shape of a separator-stripped ISBN: ISBN-10 (9 digits + digit/X check)
# or ISBN-13 (978/979 + 10 digits).
_ISBN_SHAPE_RE = re.compile(r"^[0-9]{9}[0-9X]$|^97[89][0-9]{10}$")

# A contiguous ISBN-shaped token: digit groups separated by single hyphens
# or spaces, optionally ending in the ISBN-10 `X` check character. Anchored
# against neighbouring alphanumerics so digits scattered through free text
# ("987654321 unix") cannot be re-assembled into a phantom ISBN.
_ISBN_TOKEN_RE = re.compile(r"(?<![0-9A-Za-z])\d(?:[\- ]?\d)*(?:[\- ]?[Xx])?(?![0-9A-Za-z])")


def extract_doi(query: str) -> str | None:
    """Pull a bare DOI out of a DOI URL or raw query if one is present."""
    lowered = query.lower()
    lowered = lowered.removeprefix("https://doi.org/")
    lowered = lowered.removeprefix("http://doi.org/")
    lowered = lowered.removeprefix("doi:")
    if DOI_RE.match(lowered):
        return lowered
    return None


def extract_isbn(query: str) -> str | None:
    """Pull a bare ISBN out of `ISBN: ...`, hyphenated, spaced, or plain forms.

    Accepts ISBN-10 (9 digits + check, where check may be `X`) and
    ISBN-13 (978/979 + 10 digits). The candidate must be one contiguous
    token in the query, pass the shape regex after separator stripping,
    and carry a valid check digit; anything else returns None so free
    text falls through to title search. The returned form is
    digits-only (with `X` preserved on ISBN-10).
    """
    raw = query.strip().lower()
    raw = raw.removeprefix("isbn:")
    raw = raw.removeprefix("isbn ")
    for match in _ISBN_TOKEN_RE.finditer(raw):
        candidate = match.group(0).replace("-", "").replace(" ", "").upper()
        if _ISBN_SHAPE_RE.match(candidate) and _isbn_checksum_ok(candidate):
            return candidate
    return None


def _isbn_checksum_ok(isbn: str) -> bool:
    """Validate the check digit of a shape-valid ISBN-10 / ISBN-13."""
    if len(isbn) == 10:
        # Round-trip through the `_isbn` conversion helpers: `isbn10_to_13`
        # ignores the input check digit, so converting back re-derives it.
        derived_13 = isbn10_to_13(isbn)
        return derived_13 is not None and isbn13_to_10(derived_13) == isbn
    # EAN-13 weighting, computed directly: 979-prefixed ISBNs have no
    # ISBN-10 form, so the round-trip trick doesn't cover them.
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(isbn[:12]))
    return (10 - total % 10) % 10 == int(isbn[12])
