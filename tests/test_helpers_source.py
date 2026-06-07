"""Unit tests for the Source / CSL flatteners and taken-set loading."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from quelle.cli._helpers import (
    load_taken_set,
    publication_to_csl,
    publication_to_dict,
)
from quelle.models.publication import Author, Publication


def _pub() -> Publication:
    return Publication(
        title="Attention Is All You Need",
        authors=[Author(name="Ashish Vaswani"), Author(name="Noam Shazeer")],
        year=2017,
        doi="10.48550/arxiv.1706.03762",
        venue="NeurIPS",
        kind="article",
    )


# --- publication_to_dict (Source) -----------------------------------------


def test_publication_to_dict_attaches_x_vcoeur_default_base() -> None:
    data = publication_to_dict(_pub())
    assert data["citation_key"] == "VaswaniShazeer2017"
    block = data["x_vcoeur"]
    assert block == {
        "citekey": "VaswaniShazeer2017",
        "vault_id": None,
        "vault_kind": "article",
        "confidence": None,
    }


def test_publication_to_dict_citekey_override_and_vault_kind() -> None:
    web = Publication(title="Page", source_url="https://example.com/", kind="web")
    data = publication_to_dict(web, citekey="Example2024a")
    assert data["x_vcoeur"]["citekey"] == "Example2024a"
    assert data["x_vcoeur"]["vault_kind"] == "web"
    # The BibTeX-style top-level key is independent of the minted citekey.
    assert data["citation_key"] == web.citation_key()


def test_publication_to_dict_vault_kind_document_for_none_kind() -> None:
    data = publication_to_dict(Publication(title="thing"))
    assert data["x_vcoeur"]["vault_kind"] == "document"


# --- publication_to_csl ----------------------------------------------------


def test_publication_to_csl_shape() -> None:
    csl = publication_to_csl(_pub(), citekey="VaswaniShazeer2017")
    assert csl["id"] == "VaswaniShazeer2017"
    assert csl["type"] == "article-journal"
    assert csl["title"] == "Attention Is All You Need"
    assert csl["author"] == [
        {"family": "Vaswani", "given": "Ashish"},
        {"family": "Shazeer", "given": "Noam"},
    ]
    assert csl["issued"] == {"date-parts": [[2017]]}
    assert csl["container-title"] == "NeurIPS"
    assert csl["DOI"] == "10.48550/arxiv.1706.03762"


def test_publication_to_csl_type_mapping() -> None:
    assert publication_to_csl(Publication(title="b", kind="book"))["type"] == "book"
    assert publication_to_csl(Publication(title="w", kind="web"))["type"] == "webpage"
    assert publication_to_csl(Publication(title="m", kind="media"))["type"] == "motion_picture"
    assert publication_to_csl(Publication(title="x"))["type"] == "document"


# --- load_taken_set --------------------------------------------------------


def test_load_taken_set_from_csv() -> None:
    assert load_taken_set("A2020, B2021 , ,C2022", None) == {"A2020", "B2021", "C2022"}


def test_load_taken_set_from_newline_file(tmp_path: Path) -> None:
    path = tmp_path / "taken.txt"
    path.write_text("A2020\nB2021\n\nC2022\n", encoding="utf-8")
    assert load_taken_set(None, str(path)) == {"A2020", "B2021", "C2022"}


def test_load_taken_set_from_knoten_json_file(tmp_path: Path) -> None:
    path = tmp_path / "taken.json"
    path.write_text(json.dumps({"citekeys": ["A2020", "B2021"]}), encoding="utf-8")
    assert load_taken_set(None, str(path)) == {"A2020", "B2021"}


def test_load_taken_set_union_of_csv_and_file(tmp_path: Path) -> None:
    path = tmp_path / "taken.txt"
    path.write_text("B2021\n", encoding="utf-8")
    assert load_taken_set("A2020", str(path)) == {"A2020", "B2021"}


def test_load_taken_set_from_stdin_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO('{"citekeys": ["X2030", "Y2031"]}'))
    assert load_taken_set(None, "-") == {"X2030", "Y2031"}


def test_load_taken_set_empty() -> None:
    assert load_taken_set(None, None) == set()
