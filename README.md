# PublicationManager

Local CLI that fetches academic publication metadata and PDFs from open sources, caches them locally, and hands them off to Kasten as literature notes.

Primary consumer is Claude itself via the `publications` skill; humans are a secondary audience.

## What it does

Given a publication identifier or a free-text title, `publications fetch` returns a normalised JSON blob with title, authors, year, venue, DOI, abstract, citation count and (optionally) a downloaded local PDF. It walks a fallback chain of free open sources:

| Source | Role | Rate limit |
|---|---|---|
| [OpenAlex](https://docs.openalex.org/) | Primary metadata + OA PDF URL | $1/day on free key |
| [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI-authoritative fallback (abstract, journal block) | polite pool (no hard cap) |
| [Semantic Scholar](https://api.semanticscholar.org/) | Citation graph + metadata fallback | 5000 / 5 min unauth |
| [arXiv](https://info.arxiv.org/help/api/) | Preprint metadata + direct PDFs | 1 req / 3s (enforced) |
| [Unpaywall](https://unpaywall.org/products/api) | DOI → OA PDF lookup | 100k / day |
| [`scholarly`](https://pypi.org/project/scholarly/) | Last-resort for Google Scholar URLs | optional extra |

No Scholar scraping on the primary path. See the design notes in `~/src/vcoeur/conception/projects/2026-04-14-google-scholar-kasten-ingest/`.

## Stack

Python 3.12+, `uv`-managed. Typer (CLI) + httpx (sync HTTP) + stdlib `sqlite3` (cache) + rich + environs + pytest + pytest-httpx. No GUI, no ORM, no async. Same architectural conventions as [`KastenManager`](../KastenManager/).

## Quickstart

```bash
make dev-install          # install all deps incl. dev
make test                 # pytest (80+ unit + smoke tests)
make lint                 # ruff check + format --check
make format               # ruff --fix + format
make tool-install         # install `publications` globally via uv tool install

# Copy the sample config and fill in at least PUBLICATIONS_CONTACT_EMAIL.
cp .env.example .env
```

## Usage

```bash
# Resolve by DOI (uses OpenAlex + Crossref enrichment by default).
publications fetch 10.1109/83.902291

# Resolve by arXiv id, with PDF download into .publications-state/pdfs/.
publications fetch 1706.03762 --download-pdf

# Resolve by free-text title.
publications fetch "The Perceptron: A Probabilistic Model" --json

# Bypass the local cache and force network.
publications fetch 10.xxxx/yyyy --no-cache

# Inspect the cache.
publications cache stats
publications cache list --limit 20
publications cache show 10.1109/83.902291
publications cache clear --yes
```

## Configuration

Settings are loaded from `.env` files and process env, in priority order:

1. `~/.config/publications/.env` — user-level
2. `$PUBLICATIONS_HOME/.env` — repo-level
3. Process environment variables

The only variable worth setting by default is `PUBLICATIONS_CONTACT_EMAIL` — it goes into the `User-Agent` header and enrolls you in the Crossref / OpenAlex polite pool. See [`.env.example`](.env.example) for the full list.

## Layout

```
app/
  models/        <- Publication, Author (pure dataclasses)
  repositories/
    cache.py           <- SQLite cache keyed by DOI / arXiv / title
    errors.py          <- Error hierarchy -> exit codes 1/2/3/4
    http_client.py     <- httpx + polite User-Agent
    pdf_downloader.py  <- Streaming PDF download with content-type + size checks
    sources/           <- One module per source: openalex, crossref, semantic_scholar, arxiv, unpaywall, scholar_fallback
  services/
    resolver.py         <- Source orchestration + enrichment chain + cache lookup
    pdf_resolver.py     <- Lazy PDF fallback chain
  cli/
    main.py             <- Typer app (fetch, cache, version, config)
    output.py           <- JSON vs rich TTY rendering
  settings.py           <- environs-layered config
tests/
```

Layer rules: imports only go downward. Models import nothing from this project. Repositories import models. Services import models + repositories. CLI is the wiring layer.

## Status

Phases 1-6 of the conception implementation plan are done:

- **Phase 1** — All 4 open-API sources implemented with a merge-logic enrichment chain.
- **Phase 2** — SQLite cache with DOI / arXiv / OpenAlex / title lookup; second query for the same paper is offline.
- **Phase 3** — PDF download chain (`--download-pdf`) with OpenAlex → arXiv → Unpaywall fallback, content-type + size validation.
- **Phase 4** — Optional `scholarly` extra for Google Scholar URLs (install with `uv sync --extra scholar`).
- **Phase 5** — Thin `publications` skill under `ClaudeConfig/config/skills/publications/` that chains `publications fetch` into `kasten upload` + `kasten create`.
- **Phase 6** — Polished `.env.example`, README, and error messages.

Next step: run the skill end-to-end against the 5-paper test corpus described in the conception project.
