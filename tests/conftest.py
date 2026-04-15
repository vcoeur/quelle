"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from quelle.paths import Paths
from quelle.settings import Settings


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop httpx from picking up system proxy env vars during tests."""
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    """A Settings with paths rooted under a fresh tmp_path, no secrets set."""
    config_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    config_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    test_paths = Paths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        env_file=config_dir / ".env",
        pdf_dir=data_dir / "pdfs",
        cache_db=cache_dir / "cache.sqlite",
        is_dev=False,
    )
    return Settings(
        openalex_api_key="",
        semantic_scholar_api_key="",
        unpaywall_email="",
        contact_email="tests@example.com",
        http_timeout=5.0,
        user_agent="quelle-tests/0.1.0",
        max_pdf_mb=100,
        paths=test_paths,
    )
