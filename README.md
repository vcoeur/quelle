# quelle

`quelle` is a local CLI that fetches academic publication metadata and PDFs from open academic sources (OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall) and returns them as normalised JSON. Designed as a composable building block — feed the output into any note-taking system, reference manager, or research workflow.

The name is German for *source* — in academic German, "Quelle:" is the word that introduces a bibliographic reference, and fetching from open sources is exactly what the tool does.

## What it does

Given a publication identifier or a free-text title, `quelle fetch` returns a normalised JSON blob with title, authors, year, venue, DOI, abstract, citation count and (optionally) a downloaded local PDF. It walks a fallback chain of free open sources:

| Source | Role | Rate limit |
|---|---|---|
| [OpenAlex](https://docs.openalex.org/) | Primary metadata + OA PDF URL | $1/day on free key |
| [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI-authoritative fallback (abstract, journal block) | polite pool (no hard cap) |
| [Semantic Scholar](https://api.semanticscholar.org/) | Citation graph + metadata fallback | 5000 / 5 min unauth |
| [arXiv](https://info.arxiv.org/help/api/) | Preprint metadata + direct PDFs | 1 req / 3s (enforced) |
| [Unpaywall](https://unpaywall.org/products/api) | DOI → OA PDF lookup | 100k / day |

Google Scholar URLs are **not supported**: Scholar has no public API and its Terms of Service prohibit automated access. If you only have a Scholar link, open the page, copy the paper title, and feed that to `quelle fetch` as a free-text query — OpenAlex and Crossref cover almost every paper with a DOI.

## Stack

Python 3.12+, `uv`-managed. Typer (CLI) + httpx (sync HTTP) + stdlib `sqlite3` (cache) + rich + environs + platformdirs + pytest + pytest-httpx. No GUI, no ORM, no async.

## Installation

Install from PyPI:

```bash
pipx install quelle
# or: uv tool install quelle
```

Both install `quelle` into its own isolated venv and put it on your `$PATH`. Run the one-time bootstrap to create the config, data, and cache directories and seed a default `.env`:

```bash
quelle init
quelle config edit        # opens the .env in your $EDITOR
```

### Development from a source checkout

```bash
git clone https://github.com/vcoeur/quelle.git
cd quelle
make dev-install          # uv sync --all-groups
make test                 # pytest
make lint                 # ruff check + format --check
make format               # ruff --fix + format
uv run quelle --help      # run the CLI straight from the repo
```

When run from the repo, `quelle` picks up the `.env` at the repo root and stores dev cache / PDFs under a repo-local `.dev-state/` instead of polluting your installed user data.

## Configuration

`quelle` follows each OS's standard "config dir + data dir + cache dir" layout via [`platformdirs`](https://platformdirs.readthedocs.io/):

| Role | Linux (XDG) | macOS | Windows |
|---|---|---|---|
| Config (`.env`) | `~/.config/quelle/` | `~/Library/Application Support/quelle/` | `%APPDATA%\quelle\` |
| Data (downloaded PDFs) | `~/.local/share/quelle/` | `~/Library/Application Support/quelle/` | `%LOCALAPPDATA%\quelle\` |
| Cache (sqlite index) | `~/.cache/quelle/` | `~/Library/Caches/quelle/` | `%LOCALAPPDATA%\quelle\Cache\` |

Any of the three can be overridden via env vars — useful for tests, Docker, or custom deployments:

```bash
export QUELLE_CONFIG_DIR=/etc/quelle
export QUELLE_DATA_DIR=/srv/quelle/data
export QUELLE_CACHE_DIR=/var/cache/quelle
```

Inspect the resolved paths at any time:

```bash
quelle config path        # plain output, one path per line
quelle config path --json # JSON, scriptable
quelle config show        # all values including API keys (redacted)
```

The only variable worth setting by default is `QUELLE_CONTACT_EMAIL` — it goes into the `User-Agent` header and enrolls you in the Crossref / OpenAlex polite pool. See [`.env.example`](.env.example) for the full list.

**Dev mode**: when you run `quelle` from a source checkout (`uv run quelle …` inside the repo), the `.env` at the repo root is still picked up — the same ergonomics as before — but downloaded PDFs and the cache go into a repo-local `.dev-state/` directory so your installed user data stays clean.

## Usage

```bash
# Resolve by DOI (uses OpenAlex + Crossref enrichment by default).
quelle fetch 10.1109/83.902291

# Resolve by arXiv id, with PDF download into the data dir.
quelle fetch 1706.03762 --download-pdf

# Resolve by free-text title.
quelle fetch "The Perceptron: A Probabilistic Model" --json

# Bypass the local cache and force network.
quelle fetch 10.xxxx/yyyy --no-cache

# Inspect the cache.
quelle cache stats
quelle cache list --limit 20
quelle cache show 10.1109/83.902291
quelle cache clear --yes
```

## Claude Code skill

A minimal example [`SKILL.md`](SKILL.md) ships at the repo root — drop it into `~/.claude/skills/quelle/` (or `<project>/.claude/skills/quelle/`) to use the CLI from a Claude Code session. It is deliberately thin: resolve the paper, print the metadata, stop. Adapt the last step for your own downstream workflow (Zettelkasten import, BibTeX append, etc.). The skill also includes a prominent reminder about each upstream's Terms of Service and rate limits — read it before running `quelle` at any non-trivial scale.

## Layout

```
quelle/
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
    main.py             <- Typer app (fetch, cache, version, init)
    config.py           <- `config show` / `path` / `edit` + bootstrap
    output.py           <- JSON vs rich TTY rendering
  paths.py              <- platformdirs resolution (config / data / cache)
  migrate.py            <- One-shot migration from the legacy config/cache layout
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
| [OpenAlex](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication) | CC0 — *"OpenAlex data is and will remain available at no cost"* | ~100k / day on the polite pool; single-entity lookups unlimited | not required | Provide an email via `QUELLE_CONTACT_EMAIL` for the polite pool, or set `OPENALEX_API_KEY` for the new key-based tier (OpenAlex announced in January 2026 that key authentication is replacing the mailto polite pool; the tool supports both). |
| [Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | CC0 for metadata — *"almost none of the metadata is subject to copyright, and you may use it for any purpose"*. Some abstracts may remain copyrighted. | No hard cap; the polite pool is requested via your `mailto=` / User-Agent | not required, but recommended | Commercial users who need SLAs should subscribe to Metadata Plus directly with Crossref. |
| [arXiv API](https://info.arxiv.org/help/api/tou.html) | Metadata CC0. PDFs retain their authors' / arXiv's licence. | **1 request / 3 seconds** (the tool enforces this globally via a module-level lock) | Do not claim arXiv endorses your project. | **You may not store and re-serve arXiv e-prints (PDFs, source files, other content) from your own servers unless you have the copyright holder's permission.** Downloading for local personal reading is explicitly allowed. |
| [Semantic Scholar](https://www.semanticscholar.org/product/api/license) | S2 data may be `CC BY-NC` or `ODC-BY` depending on the record. The API itself is provided *"AS IS, WITH ALL FAULTS, AND AS AVAILABLE"* with no warranty. | Public endpoints need no auth; higher throughput requires a free key from Ai2. | **Required** — *"Licensee will include an attribution to 'Semantic Scholar'"*, and publications must cite *The Semantic Scholar Open Data Platform*. | You may not *"repackage, sell, rent, lease, lend, distribute, or sublicense the API"*. This tool is a personal client, not a proxy. |
| [Unpaywall](https://unpaywall.org/products/api) | CC0 data | 100k requests / day | not required | The email parameter is **mandatory** — Unpaywall uses it to contact you if something goes wrong. Don't fake it. For bulk workloads, download the free data snapshot instead of hammering the API. |

**Google Scholar is not supported.** Google Scholar has no official API, and Google's Terms of Service prohibit automated access. Passing a Scholar URL to `quelle fetch` returns a `UserError` asking you to copy the paper title manually and retry — OpenAlex and Crossref together cover almost every paper with a DOI, so the workaround is usually one extra copy-paste.

**No warranty**: see the MIT [`LICENSE`](LICENSE) — this tool is provided as-is, with no guarantee that its JSON output is correct, complete, or current. Verify critical metadata against the canonical upstream before relying on it.

## Licence

MIT — see [`LICENSE`](LICENSE).

## Questions or feedback

This is a personal tool — I'm happy to hear from you, but there is no formal support. The best way to reach me is the contact form on [vcoeur.com](https://vcoeur.com).
