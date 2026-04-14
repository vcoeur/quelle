"""Given a resolved Publication, find and download the OA PDF.

Walks a fallback chain of possible PDF sources, stopping at the
first success. On total failure, returns a `PdfOutcome` with
`local_path=None` and a string reason the caller can log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.models.publication import Publication
from app.repositories.errors import NetworkError
from app.repositories.pdf_downloader import download_pdf
from app.repositories.sources import unpaywall
from app.settings import Settings


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


def _unpaywall_pdf_url(client: httpx.Client, settings: Settings, doi: str) -> str | None:
    """Look up a DOI in Unpaywall and return the best OA PDF URL, if any."""
    if not settings.unpaywall_email and not settings.contact_email:
        return None
    try:
        payload = unpaywall.lookup_by_doi(client, settings, doi)
    except Exception:  # noqa: BLE001
        return None
    return unpaywall.extract_pdf_url(payload)
