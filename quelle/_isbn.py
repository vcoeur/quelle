"""ISBN normalisation helpers — pure arithmetic, no I/O.

Lifted out of `services/resolver.py` so source modules can import
them without a circular dependency. Used by:

- the resolver, to backfill the missing form of an ISBN before
  caching (so a future query in either form hits the cache);
- `repositories.sources.bnf`, to post-validate that a fuzzy SRU
  match actually corresponds to the queried ISBN.
"""

from __future__ import annotations


def isbn10_to_13(isbn_10: str) -> str | None:
    """Convert an ISBN-10 to its 978-prefixed ISBN-13 form."""
    if len(isbn_10) != 10 or not isbn_10[:9].isdigit():
        return None
    body = "978" + isbn_10[:9]
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(body))
    check = (10 - total % 10) % 10
    return body + str(check)


def isbn13_to_10(isbn_13: str) -> str | None:
    """Convert a 978-prefixed ISBN-13 to its ISBN-10 form.

    Returns None for 979-prefixed ISBN-13s (no ISBN-10 equivalent).
    """
    if len(isbn_13) != 13 or not isbn_13.isdigit() or not isbn_13.startswith("978"):
        return None
    body = isbn_13[3:12]
    total = sum(int(ch) * (i + 1) for i, ch in enumerate(body))
    check = total % 11
    return body + ("X" if check == 10 else str(check))


def isbn_forms(isbn: str) -> set[str]:
    """Return the set of equivalent ISBN forms for a given input.

    For an ISBN-10 input, the set has at most two elements (the
    original ISBN-10 and the derived ISBN-13). For an ISBN-13 input
    starting with 978, both forms are returned; for a 979-prefix
    ISBN-13, only the original (no ISBN-10 equivalent exists).
    """
    forms = {isbn}
    if len(isbn) == 10:
        derived = isbn10_to_13(isbn)
        if derived:
            forms.add(derived)
    elif len(isbn) == 13:
        derived = isbn13_to_10(isbn)
        if derived:
            forms.add(derived)
    return forms
