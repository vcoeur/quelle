"""Stream a PDF from a URL to a local path.

Writes atomically via a temp file + rename. Rejects content that
isn't a PDF (either by content-type header or by magic bytes) and
aborts downloads that exceed the configured `max_pdf_mb` limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from app.repositories.errors import NetworkError
from app.settings import Settings


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a single PDF download attempt."""

    local_path: Path
    size_bytes: int
    content_type: str


_PDF_MAGIC = b"%PDF"


def download_pdf(
    client: httpx.Client,
    url: str,
    dest_path: Path,
    settings: Settings,
) -> DownloadResult:
    """Download `url` to `dest_path`. Raises `NetworkError` on any failure."""
    max_bytes = settings.max_pdf_mb * 1024 * 1024
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    try:
        with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise NetworkError(f"{response.status_code} from {url}")
            content_type = (response.headers.get("content-type") or "").lower()
            size = 0
            first_chunk_seen = False
            with tmp_path.open("wb") as handle:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    if not first_chunk_seen:
                        first_chunk_seen = True
                        if not _looks_like_pdf(chunk, content_type):
                            raise NetworkError(
                                f"not a PDF: content-type={content_type!r}, "
                                f"first bytes={chunk[:8]!r}"
                            )
                    size += len(chunk)
                    if size > max_bytes:
                        raise NetworkError(f"PDF exceeds {settings.max_pdf_mb} MB limit at {url}")
                    handle.write(chunk)
    except httpx.RequestError as exc:
        _cleanup(tmp_path)
        raise NetworkError(f"download failed: {exc}") from exc
    except NetworkError:
        _cleanup(tmp_path)
        raise

    tmp_path.replace(dest_path)
    return DownloadResult(local_path=dest_path, size_bytes=size, content_type=content_type)


def _looks_like_pdf(first_chunk: bytes, content_type: str) -> bool:
    """Accept if either the content-type advertises PDF or the magic bytes match."""
    if "application/pdf" in content_type:
        return True
    return first_chunk.startswith(_PDF_MAGIC)


def _cleanup(path: Path) -> None:
    """Best-effort removal of a partial download."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return
