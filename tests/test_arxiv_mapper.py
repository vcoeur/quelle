"""Unit tests for the arXiv Atom parser and rate limiter."""

from __future__ import annotations

import time

import pytest

from quelle.repositories.errors import NotFoundError
from quelle.repositories.sources import arxiv
from quelle.repositories.sources.arxiv import (
    _arxiv_id_from_abs_url,
    _parse_feed,
    _strip_version,
)

_VASWANI_FEED = (
    "<?xml version='1.0' encoding='UTF-8'?>\n"
    '<feed xmlns="http://www.w3.org/2005/Atom">\n'
    "  <entry>\n"
    "    <id>http://arxiv.org/abs/1706.03762v5</id>\n"
    "    <updated>2017-12-06T18:38:36Z</updated>\n"
    "    <published>2017-06-12T17:57:34Z</published>\n"
    "    <title>Attention Is All You Need</title>\n"
    "    <summary>\n"
    "      The dominant sequence transduction models are based on\n"
    "      complex recurrent or convolutional neural networks.\n"
    "    </summary>\n"
    "    <author><name>Ashish Vaswani</name></author>\n"
    "    <author><name>Noam Shazeer</name></author>\n"
    "    <author><name>Niki Parmar</name></author>\n"
    "    <author><name>Jakob Uszkoreit</name></author>\n"
    "    <author><name>Llion Jones</name></author>\n"
    "    <author><name>Aidan N. Gomez</name></author>\n"
    "    <author><name>Lukasz Kaiser</name></author>\n"
    "    <author><name>Illia Polosukhin</name></author>\n"
    '    <link href="http://arxiv.org/abs/1706.03762v5"\n'
    '          rel="alternate" type="text/html"/>\n'
    '    <link title="pdf"\n'
    '          href="http://arxiv.org/pdf/1706.03762v5"\n'
    '          rel="related" type="application/pdf"/>\n'
    "  </entry>\n"
    "</feed>\n"
)

_EMPTY_FEED = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Empty</title>
</feed>
"""


def test_strip_version_drops_suffix() -> None:
    assert _strip_version("1706.03762v5") == "1706.03762"


def test_strip_version_keeps_versionless_id() -> None:
    assert _strip_version("1706.03762") == "1706.03762"


def test_strip_version_handles_old_style_id() -> None:
    assert _strip_version("cond-mat/0503066v2") == "cond-mat/0503066"


def test_arxiv_id_from_abs_url_normalised() -> None:
    assert _arxiv_id_from_abs_url("http://arxiv.org/abs/1706.03762v5") == "1706.03762"


def test_arxiv_id_from_abs_url_returns_none_for_other_urls() -> None:
    assert _arxiv_id_from_abs_url("https://example.com/paper") is None


def test_parse_feed_maps_vaswani_paper() -> None:
    publication = _parse_feed(_VASWANI_FEED, expected_id="1706.03762")

    assert publication.title == "Attention Is All You Need"
    assert publication.year == 2017
    assert publication.arxiv_id == "1706.03762"
    assert publication.pdf_url == "http://arxiv.org/pdf/1706.03762v5"
    assert publication.is_open_access is True
    assert publication.venue == "arXiv preprint"
    assert publication.publisher == "arXiv"
    assert publication.resolved_from_chain == ["arxiv"]
    assert len(publication.authors) == 8
    assert publication.authors[0].name == "Ashish Vaswani"
    abstract = publication.abstract or ""
    assert "dominant sequence transduction" in abstract
    # Eight authors -> Al suffix
    assert publication.citation_key() == "VaswaniAl2017"


def test_parse_feed_raises_not_found_when_empty() -> None:
    with pytest.raises(NotFoundError):
        _parse_feed(_EMPTY_FEED, expected_id="nope")


def test_rate_limit_sleeps_when_called_back_to_back(monkeypatch: pytest.MonkeyPatch) -> None:
    arxiv._reset_rate_limit_for_tests()
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(arxiv.time, "sleep", fake_sleep)

    start = time.monotonic()
    arxiv._set_last_call_for_tests(start)
    arxiv._rate_limit()  # second call, same timestamp -> should sleep ~3s
    assert slept and slept[0] >= 2.9


def test_rate_limit_does_not_sleep_on_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    arxiv._reset_rate_limit_for_tests()
    slept: list[float] = []
    monkeypatch.setattr(arxiv.time, "sleep", lambda s: slept.append(s))

    arxiv._rate_limit()
    assert slept == []
