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

Google Scholar URLs are **not supported**: Scholar has no public API and its Terms of Service prohibit automated access. If you only have a Scholar link, open the page, copy the paper title, and feed that to `publications fetch` as a free-text query — OpenAlex and Crossref cover almost every paper with a DOI.

## Stack

Python 3.12+, `uv`-managed. Typer (CLI) + httpx (sync HTTP) + stdlib `sqlite3` (cache) + rich + environs + pytest + pytest-httpx. No GUI, no ORM, no async.

## Claude Code skill

A minimal example [`SKILL.md`](skills/publications/SKILL.md) ships in `skills/publications/` — drop it into `~/.claude/skills/publications/` (or `<project>/.claude/skills/publications/`) to use the CLI from a Claude Code session. It's deliberately thin: resolve the paper, print the metadata, stop. Adapt the last step for your own downstream workflow (Zettelkasten import, BibTeX append, etc.).

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
    sources/           <- One module per source: openalex, crossref, semantic_scholar, arxiv, unpaywall
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

v0.1 — all five open-API sources wired up with a merge-logic enrichment chain, a SQLite cache keyed by DOI / arXiv id / OpenAlex id / title (second query for the same paper is offline), and a PDF download chain with OpenAlex → arXiv → Unpaywall fallback plus content-type and size validation.

## Usage and terms

This tool is intended for **personal and academic research use**. It queries free, public APIs on your behalf. **You are responsible for complying with each upstream's terms of service** — the MIT licence on this repo covers the *code* of this tool, not the data you fetch through it.

**Not supported use cases**:

- **Bulk scraping** / batch ingestion of many records. Most upstreams publish free database snapshots; use those instead of hammering the live API.
- **Rehosting downloaded PDFs** on a public server. The `--download-pdf` flag writes to a local cache on your machine — that is fine. Re-serving arXiv PDFs, publisher PDFs, or full text from your own infrastructure is not (see arXiv and Semantic Scholar rows below).
- **Commercial repackaging** of the JSON output as a paid product. Individual commercial use of the metadata is generally allowed by the underlying licences, but Semantic Scholar in particular requires attribution and some S2 records are `CC BY-NC`.

**Per-source summary**:

| Source | Data licence | Rate limit | Attribution | Notes |
|---|---|---|---|---|
| [OpenAlex](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication) | CC0 — *"OpenAlex data is and will remain available at no cost"* | ~100k / day on the polite pool; single-entity lookups unlimited | not required | Provide an email via `PUBLICATIONS_CONTACT_EMAIL` for the polite pool, or set `OPENALEX_API_KEY` for the new key-based tier (OpenAlex announced in January 2026 that key authentication is replacing the mailto polite pool; the tool supports both). |
| [Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | CC0 for metadata — *"almost none of the metadata is subject to copyright, and you may use it for any purpose"*. Some abstracts may remain copyrighted. | No hard cap; the polite pool is requested via your `mailto=` / User-Agent | not required, but recommended | Commercial users who need SLAs should subscribe to Metadata Plus directly with Crossref. |
| [arXiv API](https://info.arxiv.org/help/api/tou.html) | Metadata CC0. PDFs retain their authors' / arXiv's licence. | **1 request / 3 seconds** (the tool enforces this globally via a module-level lock) | Do not claim arXiv endorses your project. | **You may not store and re-serve arXiv e-prints (PDFs, source files, other content) from your own servers unless you have the copyright holder's permission.** Downloading for local personal reading is explicitly allowed. |
| [Semantic Scholar](https://www.semanticscholar.org/product/api/license) | S2 data may be `CC BY-NC` or `ODC-BY` depending on the record. The API itself is provided *"AS IS, WITH ALL FAULTS, AND AS AVAILABLE"* with no warranty. | Public endpoints need no auth; higher throughput requires a free key from Ai2. | **Required** — *"Licensee will include an attribution to 'Semantic Scholar'"*, and publications must cite *The Semantic Scholar Open Data Platform*. | You may not *"repackage, sell, rent, lease, lend, distribute, or sublicense the API"*. This tool is a personal client, not a proxy. |
| [Unpaywall](https://unpaywall.org/products/api) | CC0 data | 100k requests / day | not required | The email parameter is **mandatory** — Unpaywall uses it to contact you if something goes wrong. Don't fake it. For bulk workloads, download the free data snapshot instead of hammering the API. |

**Google Scholar is not supported.** Google Scholar has no official API, and Google's Terms of Service prohibit automated access. Passing a Scholar URL to `publications fetch` returns a `UserError` asking you to copy the paper title manually and retry — OpenAlex and Crossref together cover almost every paper with a DOI, so the workaround is usually one extra copy-paste.

**No warranty**: see the MIT [`LICENSE`](LICENSE) — this tool is provided as-is, with no guarantee that its JSON output is correct, complete, or current. Verify critical metadata against the canonical upstream before relying on it.

## Licence

MIT — see [`LICENSE`](LICENSE).

## Questions or feedback

This is a personal tool — I'm happy to hear from you, but there is no formal support. The best way to reach me is the contact form on [vcoeur.com](https://vcoeur.com).
