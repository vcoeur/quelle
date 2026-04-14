# PublicationManager

Local CLI that fetches academic publication metadata and PDFs from open academic sources (OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall) and returns them as normalised JSON. Designed as a composable building block — feed the output into any note-taking system, reference manager, or research workflow.

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

Scholar scraping is never on the primary path — OpenAlex and Crossref cover almost every paper with a DOI.

## Stack

Python 3.12+, `uv`-managed. Typer (CLI) + httpx (sync HTTP) + stdlib `sqlite3` (cache) + rich + environs + pytest + pytest-httpx. No GUI, no ORM, no async.

## Quickstart

```bash
git clone https://github.com/vcoeur/PublicationManager.git
cd PublicationManager

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

v0.1 — all five open-API sources wired up with a merge-logic enrichment chain, a SQLite cache keyed by DOI / arXiv id / OpenAlex id / title (second query for the same paper is offline), and a PDF download chain with OpenAlex → arXiv → Unpaywall fallback plus content-type and size validation. Optional `scholarly` extra for Google Scholar URLs (install with `uv sync --extra scholar`).

## Licence

MIT — see [`LICENSE`](LICENSE).

## Questions or feedback

This is a personal tool — I'm happy to hear from you, but there is no formal support. The best way to reach me is the contact form on [vcoeur.com](https://vcoeur.com).
