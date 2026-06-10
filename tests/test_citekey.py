"""Tests for the CiteKey convention module (`quelle.services.citekey`)."""

from __future__ import annotations

import re
from datetime import datetime

from quelle.models.publication import Author, Publication
from quelle.services.citekey import base_key, mint, vault_kind

# Web/media keys date an undated source by its retrieval year (today).
_THIS_YEAR = str(datetime.now().year)


def _authored(names: list[str], year: int | None) -> Publication:
    return Publication(title="X", authors=[Author(name=n) for n in names], year=year)


# --- base_key: authored ---------------------------------------------------


def test_base_key_authored_single() -> None:
    assert base_key(_authored(["Frank Rosenblatt"], 1958)) == "Rosenblatt1958"


def test_base_key_authored_two() -> None:
    assert base_key(_authored(["Daniel Kahneman", "Amos Tversky"], 1972)) == "KahnemanTversky1972"


def test_base_key_authored_three_or_more() -> None:
    pub = _authored(["Vincent Caselles", "Ron Kimmel", "Guillermo Sapiro"], 1997)
    assert base_key(pub) == "CasellesAl1997"


def test_base_key_authored_missing_year() -> None:
    assert base_key(_authored(["Rosenblatt"], None)) == "RosenblattND"


def test_base_key_authored_ascii_folds_accents() -> None:
    assert base_key(_authored(["François Récanati"], 2020)) == "Recanati2020"


def test_base_key_authored_surname_particle_keeps_last_word() -> None:
    assert base_key(_authored(["Ferdinand de Saussure"], 1916)) == "Saussure1916"


def test_base_key_skips_whitespace_only_author_and_uses_next() -> None:
    # A blank first author must not derail the authored branch when a
    # later author is usable — citekey mirrors citation_key()'s skipping.
    assert base_key(_authored(["   ", "Frank Rosenblatt"], 1958)) == "Rosenblatt1958"


# --- base_key: web --------------------------------------------------------


def test_base_key_web_from_site_name_with_ref() -> None:
    pub = Publication(
        title="X1 Carbon",
        venue="Bambu Lab",
        year=2024,
        source_url="https://bambulab.com/en/x1",
        kind="web",
    )
    assert base_key(pub) == "BambuLab2024-x1"


def test_base_key_web_from_domain_no_ref() -> None:
    pub = Publication(title="Home", source_url="https://bambulab.com/", year=2024, kind="web")
    assert base_key(pub) == "Bambulab2024"


def test_base_key_web_no_year_uses_retrieval_year() -> None:
    pub = Publication(title="Post", source_url="https://example.com/blog/my-post", kind="web")
    assert base_key(pub) == f"Example{_THIS_YEAR}-mypost"


def test_base_key_web_github_org_repo() -> None:
    pub = Publication(
        title="knoten", source_url="https://github.com/vcoeur/knoten", year=2025, kind="web"
    )
    assert base_key(pub) == "VcoeurKnoten2025"


# --- base_key: media ------------------------------------------------------


def test_base_key_media_youtube_with_id() -> None:
    pub = Publication(
        title="Some Video",
        venue="Veritasium",
        year=2023,
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        kind="media",
    )
    assert base_key(pub) == "Veritasium2023-dQw4w9WgXcQ"


def test_base_key_media_youtu_be_short() -> None:
    pub = Publication(
        title="V", venue="Channel", year=2022, source_url="https://youtu.be/abc123", kind="media"
    )
    assert base_key(pub) == "Channel2022-abc123"


def test_base_key_media_falls_back_to_site_when_no_channel() -> None:
    pub = Publication(title="V", source_url="https://vimeo.com/12345", kind="media")
    assert base_key(pub) == f"Vimeo{_THIS_YEAR}-12345"


# --- base_key: fallbacks --------------------------------------------------


def test_base_key_authorless_other_uses_title() -> None:
    pub = Publication(title="my-research-paper", year=2021)
    assert base_key(pub) == "MyResearchPaper2021"


def test_base_key_last_resort_domain_plus_date() -> None:
    # No author, no site/venue, no title — only a URL: domain + access date.
    pub = Publication(title="", source_url="https://example.com/", kind="web")
    key = base_key(pub)
    assert re.fullmatch(rf"Example{_THIS_YEAR}|ExampleCom\d{{8}}", key), key
    # An authorless record with neither title nor URL still yields a key.
    bare = Publication(title="")
    assert re.fullmatch(r"Source\d{8}", base_key(bare))


def test_base_key_never_empty() -> None:
    assert base_key(Publication(title="")) != ""


# --- mint -----------------------------------------------------------------


def test_mint_returns_base_when_free() -> None:
    assert mint("Alice2026", set()) == "Alice2026"
    assert mint("Alice2026", {"Bob2026"}) == "Alice2026"


def test_mint_walks_suffixes() -> None:
    assert mint("Alice2026", {"Alice2026"}) == "Alice2026a"
    assert mint("Alice2026", {"Alice2026", "Alice2026a"}) == "Alice2026b"


def test_mint_skips_a_when_already_suffixed_set() -> None:
    taken = {"Alice2026", "Alice2026a", "Alice2026b", "Alice2026c"}
    assert mint("Alice2026", taken) == "Alice2026d"


def test_mint_rolls_over_to_two_letters() -> None:
    import string

    taken = {"K2026"} | {f"K2026{c}" for c in string.ascii_lowercase}
    assert mint("K2026", taken) == "K2026aa"


# --- vault_kind -----------------------------------------------------------


def test_vault_kind_map() -> None:
    assert vault_kind("article") == "article"
    assert vault_kind("preprint") == "article"
    assert vault_kind("book") == "book"
    assert vault_kind("book-chapter") == "book"
    assert vault_kind("web") == "web"
    assert vault_kind("media") == "media"


def test_vault_kind_none_and_unknown_are_document() -> None:
    assert vault_kind(None) == "document"
    assert vault_kind("thesis") == "document"
