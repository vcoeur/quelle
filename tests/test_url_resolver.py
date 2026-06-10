"""Tests for the generic URL (web/media) resolver — all HTTP mocked."""

from __future__ import annotations

import httpx

from quelle.services.url_resolver import resolve_url

_WEB_HTML = """<html><head>
<title>Fallback Title</title>
<meta property="og:title" content="The Real Title">
<meta property="og:site_name" content="Bambu Lab">
<meta property="og:type" content="website">
<meta property="article:published_time" content="2024-03-15T10:00:00Z">
</head><body>hello</body></html>"""

_SPARSE_HTML = "<html><head></head><body>nothing useful here</body></html>"

_VIDEO_HTML = """<html><head>
<title>A Talk</title>
<meta property="og:type" content="video.other">
<meta property="og:site_name" content="Conf">
</head></html>"""


def _client(html: str, *, content_type: str = "text/html") -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": content_type})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_url_extracts_title_site_year(tmp_settings) -> None:
    with _client(_WEB_HTML) as client:
        pub = resolve_url(client, tmp_settings, "https://bambulab.com/en/x1")
    assert pub.title == "The Real Title"
    assert pub.venue == "Bambu Lab"
    assert pub.year == 2024
    assert pub.kind == "web"
    assert pub.source_url == "https://bambulab.com/en/x1"


def test_resolve_url_prefers_title_tag_when_no_og_title(tmp_settings) -> None:
    html = "<html><head><title>Just A Title</title></head></html>"
    with _client(html) as client:
        pub = resolve_url(client, tmp_settings, "https://example.com/page")
    assert pub.title == "Just A Title"
    assert pub.year is None


def test_resolve_url_media_by_og_type(tmp_settings) -> None:
    with _client(_VIDEO_HTML) as client:
        pub = resolve_url(client, tmp_settings, "https://talks.example.com/watch")
    assert pub.kind == "media"


def test_resolve_url_media_by_known_host(tmp_settings) -> None:
    with _client(_SPARSE_HTML) as client:
        pub = resolve_url(client, tmp_settings, "https://www.youtube.com/watch?v=abc")
    assert pub.kind == "media"


def test_resolve_url_prefers_publication_date_over_modified_time(tmp_settings) -> None:
    """A page edited recently is still dated by its publication date."""
    html = """<html><head>
    <meta property="og:updated_time" content="2025-01-02T00:00:00Z">
    <meta property="article:modified_time" content="2024-12-01T00:00:00Z">
    <meta name="citation_publication_date" content="2019/06/01">
    </head></html>"""
    with _client(html) as client:
        pub = resolve_url(client, tmp_settings, "https://example.com/post")
    assert pub.year == 2019


def test_resolve_url_falls_back_to_modified_time(tmp_settings) -> None:
    """With no publication-date meta, a modified-time meta still dates the page."""
    html = """<html><head>
    <meta property="article:modified_time" content="2023-05-05T00:00:00Z">
    </head></html>"""
    with _client(html) as client:
        pub = resolve_url(client, tmp_settings, "https://example.com/post")
    assert pub.year == 2023


def test_host_of_lowercases_and_drops_port() -> None:
    from quelle.services.url_resolver import MEDIA_HOSTS, host_of

    assert host_of("https://WWW.YouTube.COM:443/watch?v=x") == "www.youtube.com"
    assert host_of("not a url") == ""
    assert "youtu.be" in MEDIA_HOSTS


def test_resolve_url_degrades_gracefully(tmp_settings) -> None:
    # No title, no date: still a valid web Publication with the URL as title.
    with _client(_SPARSE_HTML) as client:
        pub = resolve_url(client, tmp_settings, "https://example.com/")
    assert pub.kind == "web"
    assert pub.year is None
    assert pub.title == "https://example.com/"
    assert pub.source_url == "https://example.com/"
