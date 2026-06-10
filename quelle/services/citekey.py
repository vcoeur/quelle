"""The CiteKey naming convention — quelle owns it.

A CiteKey is a short, human-legible, vault-ready key for *any* source —
not only authored academic works. This module is the single owner of
the convention; every rule (per-kind base key, collision suffixing, and
the quelle→knoten kind map) lives here so callers never reinvent it.

Two layers:

- `base_key(pub)` — the un-disambiguated key derived purely from the
  Publication's own fields. Deterministic, never empty.
- `mint(base, taken)` — disambiguate `base` against a set of keys
  already taken in the destination vault, appending a lowercase suffix
  (`a`, `b`, …, `z`, `aa`, …) until free.

Authored works delegate to `Publication.citation_key()` — the authored
branch of the convention is *derived there*, in the model (accent
folding, `[A-Za-z0-9]` sanitisation, unusable-name skipping included),
because the models layer cannot import this module. Authorless
web/media/PDF sources get site/channel/title rules, falling back to a
deterministic domain+date key so a key is always produced.
"""

from __future__ import annotations

import itertools
import re
import string
import unicodedata
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from quelle.models.publication import Publication
from quelle.services.url_resolver import host_of

# quelle `kind` → knoten vault kind. Consumed by `x_vcoeur.vault_kind`.
# None / unknown collapses to "document".
KIND_MAP: dict[str, str] = {
    "article": "article",
    "preprint": "article",
    "book": "book",
    "book-chapter": "book",
    "web": "web",
    "media": "media",
}


def vault_kind(kind: str | None) -> str:
    """Map a quelle `kind` to its knoten vault kind.

    None or any unmapped value collapses to `"document"`.
    """
    if kind is None:
        return "document"
    return KIND_MAP.get(kind, "document")


def base_key(pub: Publication) -> str:
    """Return the un-disambiguated CiteKey for a Publication.

    Branches by source shape:

    - **Authored** (any usable author name): delegates to
      `pub.citation_key()`, where the authored branch of the
      convention (BibTeX rule + folding/sanitisation) lives.
    - **web** (`kind == "web"`, no author): `SiteNameYYYY[-ref]`.
    - **media** (`kind == "media"`, no author): `ChannelYYYY[-id]`.
    - **other authorless** (article/book/PDF with a title): a
      CamelCase-title + year key.
    - **last resort**: `RegistrableDomain + AccessDate` (or a title /
      generic stem when no URL is present).

    Never returns an empty string.
    """
    if _has_usable_author(pub):
        return pub.citation_key()
    if pub.kind == "web":
        return _web_key(pub)
    if pub.kind == "media":
        return _media_key(pub)
    return _authorless_other_key(pub)


def mint(base: str, taken: set[str]) -> str:
    """Return `base` if free, else `base` + the first free lowercase suffix.

    Walks `a, b, …, z, aa, ab, …` (bijective base-26) until a key not
    in `taken` is found. `taken` is the set of CiteKeys already in use
    in the destination vault, injected by the caller.
    """
    if base not in taken:
        return base
    for suffix in _suffixes():
        candidate = f"{base}{suffix}"
        if candidate not in taken:
            return candidate
    raise AssertionError("unreachable: suffix generator is infinite")


def _suffixes():
    """Yield `a, b, …, z, aa, ab, …` indefinitely."""
    letters = string.ascii_lowercase
    length = 1
    while True:
        for combo in itertools.product(letters, repeat=length):
            yield "".join(combo)
        length += 1


def _has_usable_author(pub: Publication) -> bool:
    """True when any author carries a non-whitespace name.

    Mirrors `Publication.citation_key()`, which skips unusable names —
    so the authored branch is taken exactly when the model can derive
    an authored key.
    """
    return any(author.name and author.name.strip() for author in pub.authors)


def _retrieval_year() -> str:
    """The current (retrieval) year — when the source was fetched."""
    return str(datetime.now().year)


def _year_or_retrieval(pub: Publication) -> str:
    """Year for web/media keys: the publication year, else the retrieval year.

    A fetched-but-undated web page or video is dated by *when it was retrieved*
    (today's year) rather than a no-date marker — it was a live source at that
    point in time.
    """
    return str(pub.year) if pub.year else _retrieval_year()


def _year_or_missing(pub: Publication) -> str:
    """Year for authored / authorless-other keys: the year, else `ND` (no date)."""
    return str(pub.year) if pub.year else "ND"


def _web_key(pub: Publication) -> str:
    """`SiteNameYYYY[-ref]` for an authorless web page.

    GitHub URLs are special-cased to `OrgRepo` (both CamelCased) so two
    repos under one org don't collide. Otherwise the site name comes
    from `venue` (typically `og:site_name`) or, failing that, the
    registrable-domain label with the TLD dropped. An optional `-ref`
    slug is taken from the last URL path segment when present.
    """
    year = _year_or_retrieval(pub)
    url = pub.source_url

    if url and host_of(url) in {"github.com", "www.github.com"}:
        org_repo = _github_org_repo(url)
        if org_repo:
            org, repo = org_repo
            return f"{_camel(org)}{_camel(repo)}{year}"

    site = _camel(pub.venue) if pub.venue else ""
    if not site and url:
        site = _camel(_domain_label(url))
    if not site:
        return _last_resort(pub)

    base = f"{site}{year}"
    ref = _url_ref(url) if url else None
    return f"{base}-{ref}" if ref else base


def _media_key(pub: Publication) -> str:
    """`ChannelYYYY[-id]` for an authorless media item.

    Channel comes from `venue` (e.g. `og:site_name`) or the
    registrable-domain label; the optional `-id` is a video / episode
    id (YouTube `v=`, `youtu.be/<id>`, or the last path segment).
    Falls back to the generic site rule when no channel is known.
    """
    year = _year_or_retrieval(pub)
    url = pub.source_url

    channel = _camel(pub.venue) if pub.venue else ""
    if not channel and url:
        channel = _camel(_domain_label(url))
    if not channel:
        return _last_resort(pub)

    base = f"{channel}{year}"
    media_id = _media_id(url) if url else None
    return f"{base}-{media_id}" if media_id else base


def _authorless_other_key(pub: Publication) -> str:
    """`CamelTitleYYYY` for an authorless article/book/PDF with a title.

    Uses the first few title words; falls back to the last-resort
    domain+date key when there is no usable title.
    """
    year = _year_or_missing(pub)
    if pub.title:
        label = _camel_words(pub.title, max_words=3)
        if label:
            return f"{label}{year}"
    return _last_resort(pub)


def _last_resort(pub: Publication) -> str:
    """Deterministic, never-empty fallback: domain + access date.

    e.g. `example.com` accessed 2026-06-07 → `ExampleCom20260607`.
    Unlike the web/media site rule, the full domain (TLD included) is
    CamelCased here. With no URL, falls back to a title stem, then a
    generic `Source` label, always suffixed with the access date.
    """
    date = datetime.now().strftime("%Y%m%d")
    if pub.source_url:
        host = _strip_www(host_of(pub.source_url))
        if host:
            return f"{_camel(host)}{date}"
    if pub.title:
        label = _camel_words(pub.title, max_words=3)
        if label:
            return f"{label}{date}"
    return f"Source{date}"


# --- string + URL helpers ------------------------------------------------


def _ascii_fold(text: str) -> str:
    """Strip accents/diacritics, leaving plain ASCII letters and digits."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _camel(text: str) -> str:
    """CamelCase a label: split on non-alphanumerics, upper-case each word's
    first character (preserving any existing internal capitals), and join.

    `"Bambu Lab"` → `"BambuLab"`, `"GitHub"` → `"GitHub"`,
    `"example.com"` → `"ExampleCom"`. Returns `""` for empty / punctuation-only
    input.
    """
    folded = _ascii_fold(text)
    tokens = [tok for tok in re.split(r"[^A-Za-z0-9]+", folded) if tok]
    return "".join(tok[0].upper() + tok[1:] for tok in tokens)


def _camel_words(text: str, *, max_words: int) -> str:
    """CamelCase only the first `max_words` words of `text`."""
    folded = _ascii_fold(text)
    tokens = [tok for tok in re.split(r"[^A-Za-z0-9]+", folded) if tok][:max_words]
    return "".join(tok[0].upper() + tok[1:] for tok in tokens)


def _alnum(text: str, *, cap: int = 24) -> str:
    """Keep only ASCII alphanumerics, capped at `cap` characters."""
    cleaned = "".join(ch for ch in _ascii_fold(text) if ch.isalnum())
    return cleaned[:cap]


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _domain_label(url: str) -> str:
    """The registrable name label of a URL, TLD dropped.

    Heuristic without a public-suffix list: take the second-to-last
    dotted label (`bambulab.com` → `bambulab`, `blog.example.com` →
    `example`). Two-part ccTLDs (e.g. `example.co.uk`) are handled
    imperfectly — the rule returns `co` there. (not verified)
    """
    host = _strip_www(host_of(url))
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) >= 2:
        return labels[-2]
    return labels[0]


def _github_org_repo(url: str) -> tuple[str, str] | None:
    """Pull `(org, repo)` from a github.com URL path, if both are present."""
    segments = [seg for seg in urlsplit(url).path.split("/") if seg]
    if len(segments) >= 2:
        return segments[0], segments[1]
    return None


def _url_ref(url: str) -> str | None:
    """A distinguishing slug from the last URL path segment, alnum-only."""
    segments = [seg for seg in urlsplit(url).path.split("/") if seg]
    if not segments:
        return None
    last = segments[-1]
    if "." in last:
        last = last.rsplit(".", 1)[0]
    return _alnum(last) or None


def _media_id(url: str) -> str | None:
    """A video / episode id for known media hosts, alnum-only.

    YouTube `?v=`, `youtu.be/<id>`, else the last path segment.
    """
    host = host_of(url)
    parts = urlsplit(url)
    if "youtube.com" in host:
        values = parse_qs(parts.query).get("v")
        if values and values[0]:
            return _alnum(values[0])
    if host == "youtu.be":
        segments = [seg for seg in parts.path.split("/") if seg]
        if segments:
            return _alnum(segments[0])
    segments = [seg for seg in parts.path.split("/") if seg]
    if segments:
        return _alnum(segments[-1])
    return None
