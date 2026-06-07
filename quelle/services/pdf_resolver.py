"""Given a resolved Publication, find and download the OA PDF.

Walks a fallback chain of possible PDF sources, stopping at the
first success. On total failure, returns a `PdfOutcome` with
`local_path=None` and a string reason the caller can log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from quelle.models.publication import Publication
from quelle.repositories.errors import NetworkError, UserError
from quelle.repositories.pdf_downloader import download_pdf
from quelle.repositories.sources import unpaywall
from quelle.settings import Settings


@dataclass
class PdfOutcome:
    """Result of attempting the PDF resolution chain for one Publication."""

    local_path: Path | None
    sources_tried: list[str] = field(default_factory=list)
    reason_if_none: str | None = None


def resolve_and_download(
    client: httpx.Client,
    settings: Settings,
    publication: Publication,
    dest_dir: Path,
) -> PdfOutcome:
    """Try each PDF source in order; stop at the first successful download.

    Each step is evaluated lazily: we never look up Unpaywall if the
    OpenAlex `pdf_url` already succeeds, because Unpaywall is itself
    a network call.
    """
    citation_key = publication.citation_key()
    dest_path = dest_dir / f"{citation_key}.pdf"
    outcome = PdfOutcome(local_path=None)
    seen: set[str] = set()

    def _attempt(source: str, url: str | None) -> bool:
        if not url or url in seen:
            return False
        seen.add(url)
        outcome.sources_tried.append(source)
        try:
            result = download_pdf(client, url, dest_path, settings)
        except NetworkError as exc:
            outcome.reason_if_none = f"{source} failed: {exc}"
            return False
        outcome.local_path = result.local_path
        outcome.reason_if_none = None
        return True

    if _attempt(publication.resolved_from_chain_head(), publication.pdf_url):
        return outcome
    if publication.arxiv_id and _attempt("arxiv", f"https://arxiv.org/pdf/{publication.arxiv_id}"):
        return outcome
    if publication.doi:
        unpaywall_url = _unpaywall_pdf_url(client, settings, publication.doi)
        if _attempt("unpaywall", unpaywall_url):
            return outcome

    if not outcome.sources_tried:
        outcome.reason_if_none = "no_oa_copy"
    return outcome


# Number of bytes scanned for an embedded metadata dictionary. The Info
# dict is usually near the file head or tail; a small head scan covers the
# common case without reading a large file into memory.
_PDF_META_SCAN_BYTES = 65536

# Best-effort, dependency-free extraction of the PDF Info dictionary.
# Works only for uncompressed metadata in plain `( ... )` literal strings;
# compressed / object-stream metadata is not decoded. (not verified)
_PDF_TITLE_RE = re.compile(rb"/Title\s*\(((?:[^()\\]|\\.)*)\)")
_PDF_CREATION_RE = re.compile(rb"/CreationDate\s*\(\s*D?:?\s*((?:19|20)\d{2})")


def resolve_local_pdf(path: Path) -> Publication:
    """Build a Publication from a local `.pdf`, degrading to the filename.

    Reads the embedded Title / CreationDate from the raw PDF bytes when
    they are stored as plain literals; otherwise falls back to the file
    name stem for the title and the file's mtime year (then the current
    year) for the year. `kind` is left unset so the source maps to the
    knoten `document` vault kind. Raises `UserError` when the path is
    not an existing file.
    """
    if not path.is_file():
        raise UserError(f"not a file: {path}")

    title, year = _extract_pdf_metadata(path)
    if not title:
        title = path.stem
    if year is None:
        try:
            year = datetime.fromtimestamp(path.stat().st_mtime).year
        except OSError:
            year = datetime.now().year

    return Publication(
        title=title,
        year=year,
        kind=None,
        local_pdf_path=str(path),
        resolved_from_chain=["local_pdf"],
    )


def _extract_pdf_metadata(path: Path) -> tuple[str | None, int | None]:
    """Best-effort (title, year) from a PDF's raw bytes. (not verified)

    Returns `(None, None)` on any read error or when no plain-literal
    Info dictionary is found.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_PDF_META_SCAN_BYTES)
    except OSError:
        return None, None

    title: str | None = None
    title_match = _PDF_TITLE_RE.search(head)
    if title_match:
        raw = title_match.group(1)
        decoded = raw.replace(rb"\(", b"(").replace(rb"\)", b")")
        text = decoded.decode("latin-1", errors="replace").strip()
        title = text or None

    year: int | None = None
    creation_match = _PDF_CREATION_RE.search(head)
    if creation_match:
        year = int(creation_match.group(1))

    return title, year


def _unpaywall_pdf_url(client: httpx.Client, settings: Settings, doi: str) -> str | None:
    """Look up a DOI in Unpaywall and return the best OA PDF URL, if any."""
    if not settings.unpaywall_email and not settings.contact_email:
        return None
    try:
        payload = unpaywall.lookup_by_doi(client, settings, doi)
    except Exception:  # noqa: BLE001
        return None
    return unpaywall.extract_pdf_url(payload)
