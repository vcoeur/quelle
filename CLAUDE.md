# CLAUDE.md — quelle

Local CLI that fetches academic publication metadata and PDFs from open sources (OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall) and returns them as normalised JSON. Designed as a composable building block — the tool has no hardcoded opinion about where results end up; downstream consumers (skills, scripts, reference managers) decide that.

The name is German for *source*: in academic citations, "Quelle:" prefixes a bibliographic reference, and fetching from open sources is what this tool does. The package distributes on PyPI under `quelle`; the command-line binary is also `quelle`.

## Project type

- **Not deployed.** Per-laptop tool, distributed via PyPI (`pip install quelle`) or `uv tool install quelle`.
- **No daemon / server.** Every invocation is a short-lived CLI process.
- **Multiple upstreams, no single source of truth.** This tool queries 5+ different open APIs and normalises their responses. Cache is a convenience, not a mirror.

## Stack

- Python 3.12+, `uv`-managed
- Typer (CLI) + httpx (sync HTTP) + stdlib `sqlite3` (cache) + rich + environs + platformdirs + pytest + pytest-httpx
- No GUI. No ORM. No async.

## Architecture

Strict layers — imports only go downward.

```
quelle/
  models/        <- Publication, Author (pure dataclasses, no I/O)
  repositories/  <- http_client, errors, sources/{openalex, crossref, ...}
  services/      <- resolver (orchestrates which source to hit first)
  cli/           <- Typer app + config sub-app + rich/JSON output helpers
  paths.py       <- platformdirs resolution (config / data / cache)
  migrate.py     <- One-shot migration from the legacy config/cache layout
  settings.py    <- environs config (uses paths.resolve internally)
```

Layer rules:

- **Models** import nothing from this project.
- **Repositories** import from models. Each source (`sources/openalex.py` etc.) is a self-contained module that returns a `Publication`.
- **Services** import from models + repositories. The `resolver` decides which source to call based on the shape of the query (DOI vs arXiv id vs free text).
- **CLI** is the wiring layer: Typer command → load Settings → build httpx client → call resolver → render via `quelle/cli/output.py`.

## Paths

`quelle/paths.py` resolves three locations via [`platformdirs`](https://platformdirs.readthedocs.io/), following each OS's conventions:

| Role | Linux (XDG) | macOS | Windows |
|---|---|---|---|
| Config (`.env`) | `~/.config/quelle/` | `~/Library/Application Support/quelle/` | `%APPDATA%\quelle\` |
| Data (`pdfs/`) | `~/.local/share/quelle/` | `~/Library/Application Support/quelle/` | `%LOCALAPPDATA%\quelle\` |
| Cache (`cache.sqlite`) | `~/.cache/quelle/` | `~/Library/Caches/quelle/` | `%LOCALAPPDATA%\quelle\Cache\` |

Override any of the three at runtime via `QUELLE_CONFIG_DIR`, `QUELLE_DATA_DIR`, `QUELLE_CACHE_DIR`. Env-var overrides always win over both dev-mode detection and platformdirs defaults — this is how tests isolate filesystem state via `tmp_path`.

### Dev mode vs installed mode

`paths.resolve()` walks up from `__file__` looking for `pyproject.toml`:

- **Dev** (running from a source checkout, e.g. `uv run quelle …`): config dir = repo root (so `.env` at the repo root is still loaded automatically), data dir = `.dev-state/`, cache dir = `.dev-state/cache/`. `.dev-state/` is gitignored.
- **Installed** (`__file__` inside `site-packages/` or a `uv tools/` venv): config / data / cache come from `platformdirs`.

The detection is a heuristic (`_looks_like_installed_location` in `paths.py`) — site-packages and uv tools venvs are recognised and force installed mode regardless of any nearby `pyproject.toml`.

## Sources

Implemented in priority order (see `quelle/repositories/sources/`). Each module exports:

- `search_by_title(client, settings, title) -> Publication`
- `fetch_by_doi(client, settings, doi) -> Publication` (where applicable)
- `_to_publication(raw) -> Publication` — private mapper, unit-tested without network

Sources never decide the resolution order themselves — the `services/resolver.py` orchestrator does.

## Rate-limit discipline

- **Crossref polite pool**: every request must carry `mailto=…` (either as a query param or a `User-Agent: …(mailto:…)` suffix). `build_client` in `quelle/repositories/http_client.py` bakes the User-Agent.
- **arXiv**: max 1 request / 3 seconds for metadata queries. Static PDF fetches from `arxiv.org/pdf/...` are unbounded.
- **Unpaywall**: 100 ms delay between requests, 100k / day quota.
- **OpenAlex**: $1/day quota when authenticated, lower unauth. Don't batch fetch without caching.

## Commands

```bash
make dev-install   # install all deps incl. dev
make test          # pytest
make lint          # ruff check + format --check
make format        # ruff check --fix + format
make tool-install  # install `quelle` globally via `uv tool install`
```

## Workflow

1. After any code change: `make format` — enforced by ruff.
2. Before committing: `make lint && make test`.
3. When adding a new CLI command: add a smoke test in `tests/test_cli_smoke.py` (or `test_cli_config.py` for `config`-subapp commands).
4. When adding a new source: add a mapper unit test that feeds a recorded fixture JSON into `_to_publication` — no network required.
5. When touching the path-resolution layer or the migration: tests in `tests/test_paths.py` and `tests/test_migrate.py` must stay green. Both use `monkeypatch` to isolate filesystem state; never hit the real user's home dir.
