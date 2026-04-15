---
title: quelle — open-source academic metadata CLI
description: Local CLI that fetches academic publication metadata and PDFs from OpenAlex, Crossref, Semantic Scholar, arXiv, and Unpaywall — as normalised JSON.
---

# quelle

<p class="tagline">Cite your sources.</p>

Local CLI that fetches academic publication metadata and PDFs from free, open academic sources — [OpenAlex](https://docs.openalex.org/), [Crossref](https://www.crossref.org/), [Semantic Scholar](https://api.semanticscholar.org/), [arXiv](https://info.arxiv.org/help/api/), and [Unpaywall](https://unpaywall.org/) — and returns them as normalised JSON. A composable building block: pipe the output into any notes system, reference manager, or research workflow.

The name is German for *source* — in academic German, "Quelle:" is the word that introduces a bibliographic reference.

## Install

```bash
pipx install quelle
# or
uv tool install quelle
```

Both install `quelle` into its own isolated venv and put it on your `$PATH`. See [Install](install.md) for the long-form guide, first-run bootstrap, and cross-OS paths.

## 60-second quickstart

```bash
# One-time setup — creates config/data/cache dirs and a commented .env.
quelle init

# Resolve by DOI — OpenAlex + Crossref enrichment chain, cached locally.
quelle fetch 10.1109/83.902291

# By arXiv id, with the PDF downloaded into your data dir.
quelle fetch 1706.03762 --download-pdf

# Or by free-text title, JSON output for piping.
quelle fetch "Attention is all you need" --json
```

Example output:

```
$ quelle fetch 1706.03762 --json | jq '.title, .authors[].name, .year'
"Attention Is All You Need"
"Ashish Vaswani"
"Noam Shazeer"
...
2017
```

Every command accepts `--json` for machine-readable output. See [Commands](commands.md) for the full reference.

## What it does

- **Fallback chain over open sources.** OpenAlex is primary; Crossref enriches the DOI-authoritative fields; Semantic Scholar fills citation graph gaps; arXiv covers preprints; Unpaywall resolves DOIs to open-access PDF URLs. Fetches merge cleanly — the CLI normalises the shape regardless of which source answered.
- **Three lookup keys.** DOI, arXiv id, or free-text title. If you only have a Google Scholar link, paste the paper title instead — Scholar has no API and its ToS prohibits scraping.
- **SQLite cache.** Keyed by DOI, arXiv id, OpenAlex id, and normalised title — the second query for the same paper is offline. `quelle cache stats` / `list` / `show` / `clear` inspect and manage it.
- **PDF download chain.** `--download-pdf` tries OpenAlex → arXiv → Unpaywall in order, with content-type and size validation. arXiv's 1-req-per-3s rate limit is enforced globally via a module-level lock so parallel calls stay polite.
- **Scriptable.** All commands take `--json`, exit codes map to an error hierarchy (1 user error, 2 source error, 3 network, 4 internal), and the whole thing is designed to pipe into notes workflows or reference managers.

## Why quelle

You want to grab a paper's metadata without opening a browser, without a paid API key, and without writing source-specific HTTP calls yourself. `quelle` wraps the free open-access ecosystem behind one command and hands you normalised JSON. Good for personal research notes, Claude skills that need to resolve a paper mid-session, and anyone who wants "I have a DOI → I have a Publication" in one step.

## Usage and terms

`quelle` is intended for **personal and academic research use** — it queries free public APIs on your behalf, and **you are responsible for complying with each upstream's terms of service**. Bulk scraping, rehosting downloaded PDFs, and commercial repackaging of the output are not supported use cases. See the [README](https://github.com/vcoeur/quelle#usage-and-terms) for the per-source licence / rate-limit summary.

## Learn more

- [Install guide](install.md) — prerequisites, first-run bootstrap, cross-OS paths, dev mode
- [Commands](commands.md) — full CLI reference
- [Source on GitHub](https://github.com/vcoeur/quelle)
- [`quelle` on PyPI](https://pypi.org/project/quelle/)
