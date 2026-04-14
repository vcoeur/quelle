"""Last-resort fallback for Google Scholar URLs.

We never use Google Scholar as a normal resolution path — Scholar
has no API, the `scholarly` library is CAPTCHA-prone, and there's
no way to make it reliable without paid proxies. But if the user
pastes a Scholar URL (or a cluster id) as their only handle on a
paper, we make exactly one `scholarly` call to recover the title,
then immediately pivot back to OpenAlex for everything else.

`scholarly` is an optional extra; importing this module does not
import it. If the user hasn't installed `pip install publication-
manager[scholar]`, attempting to use this fallback raises
`ConfigError` with an install hint.
"""

from __future__ import annotations

from app.repositories.errors import ConfigError, NotFoundError


def extract_title_from_scholar_url(url: str) -> str:
    """Return the paper title for a Scholar URL, using `scholarly`.

    Exactly one network call. Any failure — `scholarly` not
    installed, CAPTCHA block, network error, empty result — maps to
    a clean `ConfigError` or `NotFoundError`.
    """
    try:
        from scholarly import scholarly
    except ImportError as exc:
        raise ConfigError(
            "Scholar fallback requires the `scholarly` extra — "
            "install with `uv pip install publication-manager[scholar]`"
        ) from exc

    try:
        result = scholarly.search_single_pub(url)
    except Exception as exc:  # noqa: BLE001  — scholarly raises assorted types
        raise NotFoundError(f"Scholar fallback failed for {url}: {exc}") from exc

    bib = (result or {}).get("bib") or {}
    title = bib.get("title") or bib.get("Title")
    if not title:
        raise NotFoundError(f"Scholar result for {url} has no title field")
    return str(title)
