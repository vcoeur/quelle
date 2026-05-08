---
title: quelle — open-source academic metadata CLI
description: Local CLI that fetches academic publication and book metadata + PDFs from OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall, Open Library, Google Books, and BnF — as normalised JSON.
---

# quelle

<p class="tagline">Cite your sources.</p>

Local CLI that fetches academic publication and book metadata and PDFs from free, open sources — [OpenAlex](https://docs.openalex.org/), [Crossref](https://www.crossref.org/), [Semantic Scholar](https://api.semanticscholar.org/), [arXiv](https://info.arxiv.org/help/api/), [Unpaywall](https://unpaywall.org/), [Open Library](https://openlibrary.org/), [Google Books](https://developers.google.com/books/), and [BnF](https://api.bnf.fr/) — and returns them as normalised JSON. Handles both academic articles (DOI / arXiv id / title) and books (ISBN / title). A composable building block: pipe the output into any notes system, reference manager, or research workflow.

The name is German for *source* — in academic German, "Quelle:" is the word that introduces a bibliographic reference.

## Install

```bash
pipx install quelle
# or
uv tool install quelle
```

Both install `quelle` into its own isolated venv and put it on your `$PATH`. See [Install](install.md) for the long-form guide, first-run setup, and cross-OS paths.

## 60-second quickstart

```bash
# Optional one-time .env seed. The data / cache dirs are created on demand.
quelle config edit

# Resolve by DOI — OpenAlex + Crossref enrichment chain, cached locally.
quelle fetch 10.1109/83.902291

# By arXiv id, with the PDF downloaded into your data dir.
quelle fetch 1706.03762 --download-pdf

# Or by free-text title, JSON output for piping (note: --json before the subcommand).
quelle --json fetch "Attention is all you need"
```

Example output:

```
$ quelle --json fetch 1706.03762 | jq '.title, .authors[].name, .year'
"Attention Is All You Need"
"Ashish Vaswani"
"Noam Shazeer"
...
2017
```

`--json` is a top-level flag — place it before the subcommand. See [Commands](commands.md) for the full reference.

## What it does

- **Fallback chains over open sources.** Articles walk OpenAlex → Crossref → Semantic Scholar (with arXiv covering preprints and Unpaywall resolving DOIs to open-access PDF URLs). Books walk Open Library → Google Books → BnF → OpenAlex. Fetches merge cleanly — the CLI normalises the shape regardless of which source answered.
- **Four lookup keys.** DOI, arXiv id, ISBN-10/13, or free-text title. Pass `--book` / `--article` to bias an ambiguous title query. If you only have a Google Scholar link, paste the paper title instead — Scholar has no API and its ToS prohibits scraping.
- **SQLite cache.** Keyed by DOI, arXiv id, OpenAlex id, ISBN-10/13, and normalised title — the second query for the same paper is offline. `quelle cache list` / `show` / `clear` inspect and manage it.
- **PDF download chain.** `--download-pdf` tries OpenAlex → arXiv → Unpaywall in order, with content-type and size validation. arXiv's 1-req-per-3s rate limit and Unpaywall's 100 ms inter-call interval are enforced globally via module-level locks so parallel calls stay polite.
- **Scriptable.** All commands take `--json`, exit codes map to an error hierarchy (1 user/not-found, 2 network, 3 cache, 4 config), and the whole thing is designed to pipe into notes workflows or reference managers.

## Why quelle

You want to grab a paper's metadata without opening a browser, without a paid API key, and without writing source-specific HTTP calls yourself. `quelle` wraps the free open-access ecosystem behind one command and hands you normalised JSON. Good for personal research notes, Claude skills that need to resolve a paper mid-session, and anyone who wants "I have a DOI → I have a Publication" in one step.

## Using quelle with knoten

`quelle` is a building block — it resolves a source, hands you JSON, and stops. The natural downstream is a note system. [`knoten`](https://knoten.vcoeur.com) — the companion CLI zettelkasten — consumes a `quelle` JSON envelope as the body of a reference note and lets you attach the downloaded PDF as a file note with a matching citation key.

```bash
# 1. Resolve the paper.
quelle --json fetch 10.1109/83.902291 > /tmp/paper.json

# 2. Derive a citation key.
cite=$(jq -r '.authors[0].family + (.year | tostring)' /tmp/paper.json)
title=$(jq -r '.title' /tmp/paper.json)

# 3. Create a reference note in the knoten vault.
knoten create \
  --filename "${cite}= ${title}" \
  --frontmatter-file /tmp/paper.json \
  --body "$(jq -r '.abstract // ""' /tmp/paper.json)" \
  --json

# 4. If an open-access PDF is available, attach it.
quelle fetch 10.1109/83.902291 --download-pdf
knoten upload "<quelle-data-dir>/pdfs/<filename>.pdf" \
  --filename "${cite}+ ${title}.pdf" \
  --json
```

The reference note and the file note share the citation key, so `knoten backlinks "${cite}= ${title}"` surfaces the attachment alongside every literature note that cites the source. See the knoten [Vault structure](https://knoten.vcoeur.com/vault-structure/#source-ingest-with-quelle) page for the full reference/literature/file-note convention, citation-key rules, and the decision rule for when a source deserves the reference + literature split versus a single journal note.

## Usage and terms

`quelle` is intended for **personal and academic research use** — it queries free public APIs on your behalf, and **you are responsible for complying with each upstream's terms of service**. Bulk scraping, rehosting downloaded PDFs, and commercial repackaging of the output are not supported use cases. See the [README](https://github.com/vcoeur/quelle#usage-and-terms) for the per-source licence / rate-limit summary.

## Learn more

- [Install guide](install.md) — prerequisites, first-run bootstrap, cross-OS paths, dev mode
- [Commands](commands.md) — full CLI reference
- [`knoten`](https://knoten.vcoeur.com) — companion CLI zettelkasten that consumes `quelle` output into reference + file notes
- [Source on GitHub](https://github.com/vcoeur/quelle)
- [`quelle` on PyPI](https://pypi.org/project/quelle/)
