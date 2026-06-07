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


def test_resolve_url_degrades_gracefully(tmp_settings) -> None:
    # No title, no date: still a valid web Publication with the URL as title.
    with _client(_SPARSE_HTML) as client:
        pub = resolve_url(client, tmp_settings, "https://example.com/")
    assert pub.kind == "web"
    assert pub.year is None
    assert pub.title == "https://example.com/"
    assert pub.source_url == "https://example.com/"
