"""Tests for `quelle._identifiers` — the single owner of identifier recognition.

DOI / ISBN extraction tests moved here from `test_resolver.py` when the
helpers were lifted out of the resolver/cache duplication, plus the
regression cases for ISBN digit-scatter false positives, checksum
validation, and old-style arXiv ids.
"""

from __future__ import annotations

import pytest

from quelle._identifiers import ARXIV_ID_RE, extract_doi, extract_isbn


def test_extract_doi_bare() -> None:
    assert extract_doi("10.1234/abcd") == "10.1234/abcd"


def test_extract_doi_url() -> None:
    assert extract_doi("https://doi.org/10.1234/abcd") == "10.1234/abcd"


def test_extract_doi_prefix() -> None:
    assert extract_doi("doi:10.1234/abcd") == "10.1234/abcd"


def test_extract_doi_lowercased() -> None:
    assert extract_doi("10.1234/ABCD") == "10.1234/abcd"


def test_extract_doi_rejects_non_doi() -> None:
    assert extract_doi("attention is all you need") is None


def test_extract_doi_rejects_arxiv_id() -> None:
    assert extract_doi("1706.03762") is None


def test_extract_isbn_13_plain() -> None:
    assert extract_isbn("9782070407132") == "9782070407132"


def test_extract_isbn_13_hyphenated() -> None:
    assert extract_isbn("978-2-07-040713-2") == "9782070407132"


def test_extract_isbn_13_space_separated() -> None:
    assert extract_isbn("978 2 07 040713 2") == "9782070407132"


def test_extract_isbn_with_isbn_prefix() -> None:
    assert extract_isbn("ISBN: 0-14-018633-6") == "0140186336"
    assert extract_isbn("isbn 9780140186338") == "9780140186338"


def test_extract_isbn_10_with_x_check_digit() -> None:
    assert extract_isbn("020161622X") == "020161622X"


def test_extract_isbn_979_prefix_with_valid_checksum() -> None:
    # 979-prefixed ISBN-13s have no ISBN-10 form — checksum is validated
    # via the direct EAN-13 computation.
    assert extract_isbn("9791032300008") == "9791032300008"


def test_extract_isbn_rejects_doi() -> None:
    assert extract_isbn("10.1234/abcd") is None


def test_extract_isbn_rejects_arxiv_id() -> None:
    assert extract_isbn("1706.03762") is None


def test_extract_isbn_rejects_short_digit_run() -> None:
    assert extract_isbn("12345678") is None


def test_extract_isbn_rejects_isbn13_with_wrong_prefix() -> None:
    # 977 is the magazine prefix, not a book — ISBN-13 must start 978/979.
    assert extract_isbn("9770000000000") is None


def test_extract_isbn_rejects_digit_scatter_free_text() -> None:
    # Regression: digits + a stray `x` scattered across free text used to
    # be re-assembled into a phantom ISBN-10 ("987654321X") and mis-route
    # the query to the book chain instead of title search.
    assert extract_isbn("987654321 unix") is None


def test_extract_isbn_rejects_digit_groups_split_by_words() -> None:
    # The old global digit-strip glued "978" + "2070407132" into the valid
    # ISBN-13 9782070407132; word-separated groups must not combine.
    assert extract_isbn("python 978 essentials 2070407132") is None


def test_extract_isbn_rejects_bad_isbn13_checksum() -> None:
    # Valid form ends in ...2; flip the check digit.
    assert extract_isbn("9782070407133") is None


def test_extract_isbn_rejects_bad_isbn10_checksum() -> None:
    # Valid form ends in ...6; flip the check digit.
    assert extract_isbn("0140186335") is None


def test_extract_isbn_rejects_bad_x_check_digit() -> None:
    # ISBN-10-shaped with a contiguous trailing X whose checksum fails.
    assert extract_isbn("987654321X") is None


@pytest.mark.parametrize(
    "arxiv_id",
    [
        "1706.03762",
        "1706.03762v5",
        "2301.12345",
        "math/0211159",
        "hep-th/9901001v2",
        "math.GT/0309136",
    ],
)
def test_arxiv_id_re_accepts_all_id_styles(arxiv_id: str) -> None:
    assert ARXIV_ID_RE.match(arxiv_id)


@pytest.mark.parametrize(
    "non_id",
    [
        "attention is all you need",
        "10.1234/abcd",
        "9782070407132",
        "math.geometry/0309136",
    ],
)
def test_arxiv_id_re_rejects_non_ids(non_id: str) -> None:
    assert ARXIV_ID_RE.match(non_id) is None
