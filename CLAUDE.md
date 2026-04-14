# CLAUDE.md — PublicationManager

Local CLI that fetches academic publication metadata and PDFs from open sources (OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall) and returns them as normalised JSON. Designed as a composable building block — the tool has no hardcoded opinion about where results end up; downstream consumers (skills, scripts, reference managers) decide that.

## Project type

- **Not deployed.** Per-laptop tool, installed globally via `uv tool install .`.
- **No daemon / server.** Every invocation is a short-lived CLI process.
- **Multiple upstreams, no single source of truth.** This tool queries 5+ different open APIs and normalises their responses. Cache is a convenience, not a mirror.

## Stack

- Python 3.12+, `uv`-managed
- Typer (CLI) + httpx (sync HTTP) + stdlib `sqlite3` (cache) + rich + environs + pytest + pytest-httpx
- No GUI. No ORM. No async.

## Architecture

Strict layers — imports only go downward.

```
app/
  models/        <- Publication, Author (pure dataclasses, no I/O)
  repositories/  <- http_client, errors, sources/{openalex, crossref, ...}
  services/      <- resolver (orchestrates which source to hit first)
  cli/           <- Typer app + rich/JSON output helpers
  settings.py    <- environs config
```

Layer rules:

- **Models** import nothing from this project.
- **Repositories** import from models. Each source (`sources/openalex.py` etc.) is a self-contained module that returns a `Publication`.
- **Services** import from models + repositories. The `resolver` decides which source to call based on the shape of the query (DOI vs arXiv id vs free text).
- **CLI** is the wiring layer: Typer command → load Settings → build httpx client → call resolver → render via `app/cli/output.py`.

## Paths

Default layout — `PUBLICATIONS_HOME` anchors a state dir:

- `$PUBLICATIONS_HOME/.publications-state/cache.sqlite` — metadata cache (gitignored)
- `$PUBLICATIONS_HOME/.publications-state/pdfs/` — downloaded PDFs (gitignored)
- `$PUBLICATIONS_HOME/.env` — API keys + contact email (gitignored)

### How `PUBLICATIONS_HOME` is resolved

- **Dev** (`uv run publications …`, `make` from the repo): `_default_home()` walks up from `__file__` and finds the repo root via `pyproject.toml`.
- **Installed** (`uv tool install .` → `~/.local/bin/publications`): `__file__` is in a uv tools venv, so the walk is skipped and the tool falls back to `~/.publications`. `~/.config/publications/.env` is layered first so the installed CLI can still point at a repo vault.

## Sources

Implemented in priority order (see `app/repositories/sources/`). Each module exports:

- `search_by_title(client, settings, title) -> Publication`
- `fetch_by_doi(client, settings, doi) -> Publication` (where applicable)
- `_to_publication(raw) -> Publication` — private mapper, unit-tested without network

Sources never decide the resolution order themselves — the `services/resolver.py` orchestrator does.

## Rate-limit discipline

- **Crossref polite pool**: every request must carry `mailto=…` (either as a query param or a `User-Agent: …(mailto:…)` suffix). `build_client` in `app/repositories/http_client.py` bakes the User-Agent.
- **arXiv**: max 1 request / 3 seconds for metadata queries. Static PDF fetches from `arxiv.org/pdf/...` are unbounded.
- **Unpaywall**: 100 ms delay between requests, 100k / day quota.
- **OpenAlex**: $1/day quota when authenticated, lower unauth. Don't batch fetch without caching.

## Commands

```bash
make dev-install   # install all deps incl. dev
make test          # pytest
make lint          # ruff check + format --check
make format        # ruff check --fix + format
make tool-install  # install `publications` globally via `uv tool install`
```

## Workflow

1. After any code change: `make format` — enforced by ruff.
2. Before committing: `make lint && make test`.
3. When adding a new CLI command: add a smoke test in `tests/test_cli_smoke.py`.
4. When adding a new source: add a mapper unit test that feeds a recorded fixture JSON into `_to_publication` — no network required.
