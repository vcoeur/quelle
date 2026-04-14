"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import Settings


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
    """A Settings pointing at a fresh tmp_path, no secrets set."""
    state_dir = tmp_path / ".publications-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    return Settings(
        openalex_api_key="",
        semantic_scholar_api_key="",
        unpaywall_email="",
        contact_email="tests@example.com",
        http_timeout=5.0,
        user_agent="PublicationManager-tests/0.1.0",
        max_pdf_mb=100,
        home=tmp_path,
        state_dir=state_dir,
        cache_db=state_dir / "cache.sqlite",
        pdf_dir=state_dir / "pdfs",
    )
