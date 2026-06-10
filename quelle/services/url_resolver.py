"""Resolve an arbitrary http(s) URL into a normalised `Publication`.

For pages that are not academic records (a blog post, a product page,
a YouTube video) we cannot consult OpenAlex / Crossref. Instead we
fetch the HTML and read its Open Graph + standard meta tags to build a
`web` or `media` Publication.

Stdlib only — the HTML is parsed with `html.parser`, no new dependency.
Degrades gracefully: a page with no `<title>` and no date still yields
a valid Publication (the CiteKey rules fall back to a domain+date key).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from quelle.models.publication import Publication
from quelle.repositories.http_client import get_text
from quelle.settings import Settings

# Hosts whose pages are treated as media (video / audio) rather than
# generic web pages. Host classification lives here, with the URL
# resolver; the CiteKey module imports it for its media-id rules.
MEDIA_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "vimeo.com",
        "www.vimeo.com",
        "player.vimeo.com",
        "podcasts.apple.com",
        "open.spotify.com",
        "soundcloud.com",
        "www.soundcloud.com",
        "twitch.tv",
        "www.twitch.tv",
        "dailymotion.com",
        "www.dailymotion.com",
    }
)

# Meta keys, in priority order, that may carry a publication date.
# Publication-date metas rank above modified-time metas: a page edited
# yesterday should still be dated by when it was published.
_DATE_META_KEYS: tuple[str, ...] = (
    "article:published_time",
    "citation_publication_date",
    "citation_date",
    "dc.date",
    "dcterms.date",
    "date",
    "datepublished",
    "publishdate",
    "article:modified_time",
    "og:updated_time",
)

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def host_of(url: str) -> str:
    """Lower-cased hostname of `url` (no port), or `""`."""
    return (urlsplit(url).hostname or "").lower()


class _MetaExtractor(HTMLParser):
    """Collect `<meta>` property/name→content pairs and the `<title>` text.

    First value wins for each meta key. Only the first non-empty
    `<title>` data run is kept.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: dict[str, str] = {}
        self.title_text: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            attr = {name.lower(): (value or "") for name, value in attrs}
            key = attr.get("property") or attr.get("name")
            content = attr.get("content")
            if key and content:
                key = key.lower()
                if key not in self.metas:
                    self.metas[key] = content
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title_text is None:
            stripped = data.strip()
            if stripped:
                self.title_text = stripped


def resolve_url(client: httpx.Client, settings: Settings, url: str) -> Publication:
    """Fetch `url`, parse its HTML metadata, and return a Publication.

    Sets `kind="media"` when the host is a known video/podcast host or
    the page advertises a video/audio `og:type`; otherwise `kind="web"`.
    A network failure propagates as `NetworkError` (exit code 2); a
    parseable-but-sparse page still yields a valid Publication.
    """
    html_text = get_text(client, url)
    parser = _MetaExtractor()
    parser.feed(html_text)
    metas = parser.metas

    title = metas.get("og:title") or parser.title_text or url
    site = metas.get("og:site_name")
    og_type = (metas.get("og:type") or "").lower()
    year = _extract_year(metas)
    kind = "media" if _is_media(url, og_type) else "web"

    return Publication(
        title=title.strip(),
        year=year,
        venue=site.strip() if site else None,
        source_url=url,
        kind=kind,
        resolved_from_chain=["url"],
    )


def _is_media(url: str, og_type: str) -> bool:
    """True when the host is a known media host or `og:type` is video/audio."""
    if host_of(url) in MEDIA_HOSTS:
        return True
    return og_type.startswith(("video", "music")) or "audio" in og_type


def _extract_year(metas: dict[str, str]) -> int | None:
    """Pull a 4-digit year out of the first date-bearing meta tag."""
    for key in _DATE_META_KEYS:
        value = metas.get(key)
        if not value:
            continue
        match = _YEAR_RE.search(value)
        if match:
            return int(match.group(0))
    return None
