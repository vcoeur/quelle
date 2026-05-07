"""BnF (Bibliothèque nationale de France) SRU client.

Strong source for French-language books; coverage of non-French
material is patchy. Uses the SRU 1.2 endpoint with the simpler
Dublin Core record schema (rather than INTERMARC) to keep parsing
tractable.

Endpoint and CQL syntax: https://api.bnf.fr/api-sru-de-bnf-catalogue-general
                         (URL conventions confirmed in-session)

Single record fetch:
    /api/SRU?version=1.2&operation=searchRetrieve
            &query=bib.isbn adj "<isbn>"
            &recordSchema=dublincore
            &maximumRecords=1
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import httpx

from quelle._isbn import isbn_forms
from quelle.models.publication import Author, Publication
from quelle.models.search import SearchHit
from quelle.repositories.errors import NetworkError, NotFoundError
from quelle.repositories.http_client import get_text
from quelle.settings import Settings

SRU_URL = "https://catalogue.bnf.fr/api/SRU"
SOURCE_NAME = "bnf"

_NS = {
    "srw": "http://www.loc.gov/zing/srw/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def fetch_by_isbn(client: httpx.Client, settings: Settings, isbn: str) -> Publication:
    """Return the BnF Dublin Core record for a specific ISBN.

    Uses the `bib.fuzzyIsbn` index rather than `bib.isbn` — the former
    accepts ISBN-10, ISBN-13, and hyphenated forms transparently,
    while the latter only matches the exact ISBN-10 form stored in
    the catalogue. The trade-off is that fuzzyIsbn falls back to
    numerically-close matches when the queried ISBN is absent
    (verified live: 9782070407132, a Folio re-edition not in the
    catalogue, returned an unrelated `Histoire des religions` whose
    ISBN-10 differs only in the last few digits). We post-validate
    that the returned record actually carries the queried ISBN in
    one of its forms; on mismatch the caller falls through to the
    next source in the resolver chain.
    """
    del settings  # BnF SRU needs no auth
    publication = _query(
        client,
        f'bib.fuzzyIsbn adj "{isbn}"',
        not_found=f"no BnF record for ISBN: {isbn}",
    )
    queried = isbn_forms(isbn)
    returned = {value for value in (publication.isbn_10, publication.isbn_13) if value}
    if not (queried & returned):
        raise NotFoundError(
            f"BnF fuzzy match returned a different ISBN ({returned!r}) "
            f"than the queried {isbn!r}; treating as a miss"
        )
    return publication


def search_by_title(client: httpx.Client, settings: Settings, title: str) -> Publication:
    """Return the top BnF match for a title query."""
    del settings
    escaped = title.replace('"', "")
    return _query(
        client,
        f'bib.title adj "{escaped}"',
        not_found=f"no BnF match for title: {title!r}",
    )


def search(
    client: httpx.Client,
    settings: Settings,
    query: str,
    *,
    author: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Return up to `limit` candidate book hits for a free-text title query."""
    del settings
    title_clause = f'bib.title adj "{query.replace(chr(34), "")}"'
    if author:
        cql = f'{title_clause} and bib.author adj "{author.replace(chr(34), "")}"'
    else:
        cql = title_clause
    params = {
        "version": "1.2",
        "operation": "searchRetrieve",
        "query": cql,
        "recordSchema": "dublincore",
        "maximumRecords": str(limit),
    }
    body = get_text(client, SRU_URL, params=params)
    return _records_to_search_hits(body)


def _records_to_search_hits(body: str) -> list[SearchHit]:
    """Parse the SRU envelope and return all DC records as SearchHits."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NetworkError(f"invalid BnF SRU response: {exc}") from exc

    hits: list[SearchHit] = []
    for rank, record in enumerate(root.findall(".//srw:record/srw:recordData/oai_dc:dc", _NS)):
        fields = _record_fields(record)
        hits.append(_to_search_hit(fields, rank))
    return hits


def _record_fields(record: ET.Element) -> dict[str, list[str]]:
    """Collect a `<dc>` element's children into a flat tag→values dict."""
    fields: dict[str, list[str]] = {}
    for child in record:
        tag = child.tag.split("}", 1)[1] if "}" in child.tag else child.tag
        text = (child.text or "").strip()
        if not text:
            continue
        fields.setdefault(tag, []).append(text)
    return fields


def _to_search_hit(record: dict[str, list[str]], rank: int) -> SearchHit:
    """Map a flat DC field dict into a SearchHit."""
    isbn_10, isbn_13, ark_url = _extract_identifiers(record.get("identifier") or [])
    creators = record.get("creator") or []
    authors = [Author(name=_clean_creator(name)) for name in creators if name]

    return SearchHit(
        title=_first(record.get("title")) or "",
        authors=authors,
        year=_extract_year(record.get("date")),
        type="book",
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        source=SOURCE_NAME,
        source_id=ark_url or "",
        raw_rank=rank,
    )


def _query(client: httpx.Client, cql: str, *, not_found: str) -> Publication:
    """Run a CQL query and map the first record."""
    params = {
        "version": "1.2",
        "operation": "searchRetrieve",
        "query": cql,
        "recordSchema": "dublincore",
        "maximumRecords": "1",
    }
    body = get_text(client, SRU_URL, params=params)
    record = _first_record(body, not_found_msg=not_found)
    return _to_publication(record)


def _first_record(body: str, *, not_found_msg: str) -> dict[str, Any]:
    """Parse the SRU envelope and return the first DC record as a flat dict.

    The DC schema permits multiple values per element name (multiple
    `<dc:creator>`, `<dc:subject>`, etc.). We collect them into lists,
    then let the mapper pick what it needs.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NetworkError(f"invalid BnF SRU response: {exc}") from exc

    record = root.find(".//srw:record/srw:recordData/oai_dc:dc", _NS)
    if record is None:
        record = root.find(".//oai_dc:dc", _NS)
    if record is None:
        raise NotFoundError(not_found_msg)

    fields: dict[str, list[str]] = {}
    for child in record:
        tag = child.tag.split("}", 1)[1] if "}" in child.tag else child.tag
        text = (child.text or "").strip()
        if not text:
            continue
        fields.setdefault(tag, []).append(text)
    return fields


def _to_publication(record: dict[str, list[str]]) -> Publication:
    """Map a flat DC field dict into a Publication.

    Dublin Core is loose — `dc:identifier` may carry an ISBN, an ARK
    URL, an ISSN, or all three; `dc:date` may be a four-digit year
    or a free-text string. The mapper tolerates the variability.
    """
    isbn_10, isbn_13, ark_url = _extract_identifiers(record.get("identifier") or [])

    creators = record.get("creator") or []
    authors = [Author(name=_clean_creator(name)) for name in creators if name]

    return Publication(
        title=_first(record.get("title")) or "",
        authors=authors,
        year=_extract_year(record.get("date")),
        publisher=_first(record.get("publisher")),
        isbn_10=isbn_10,
        isbn_13=isbn_13,
        kind="book",
        subjects=list(record.get("subject") or []),
        abstract=_first(record.get("description")),
        source_url=ark_url,
        resolved_from_chain=["bnf"],
    )


def _first(value: list[str] | None) -> str | None:
    return value[0] if value else None


def _clean_creator(raw: str) -> str:
    """Drop the `(YYYY-YYYY)` life-dates suffix the BnF appends to authors."""
    if "(" in raw:
        return raw.split("(", 1)[0].strip()
    return raw.strip()


def _extract_identifiers(values: list[str]) -> tuple[str | None, str | None, str | None]:
    """Pull ISBN-10 / ISBN-13 / ARK URL out of `dc:identifier` strings."""
    isbn_10: str | None = None
    isbn_13: str | None = None
    ark_url: str | None = None
    for raw in values:
        digits = "".join(ch for ch in raw if ch.isdigit() or ch in "Xx")
        if raw.lower().startswith("http") and "ark:" in raw:
            ark_url = raw
            continue
        if "ISBN" in raw.upper() or len(digits) in (10, 13):
            if len(digits) == 13 and digits.isdigit():
                isbn_13 = digits
            elif len(digits) == 10:
                isbn_10 = digits
    return isbn_10, isbn_13, ark_url


def _extract_year(values: list[str] | None) -> int | None:
    """Best-effort year extraction from BnF's free-form date strings."""
    for raw in values or []:
        for token in raw.replace(",", " ").split():
            if len(token) == 4 and token.isdigit():
                return int(token)
    return None
