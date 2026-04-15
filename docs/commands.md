---
title: Commands · quelle
description: Full CLI reference for quelle — fetch, cache, config, init.
---

# Commands

Every command accepts `--json` for machine-readable output. On a TTY without `--json`, output is rendered with rich tables and highlighted snippets. Claude skills and shell pipelines should always pass `--json`.

## `quelle fetch`

Resolve a publication by DOI, arXiv id, or free-text title. Walks the source fallback chain (OpenAlex → Crossref enrichment → Semantic Scholar → arXiv → Unpaywall) and returns a normalised JSON `Publication`.

```bash
# DOI — OpenAlex primary, Crossref enrichment.
quelle fetch 10.1109/83.902291
quelle fetch 10.1109/83.902291 --json

# arXiv id — preprint metadata + direct PDF.
quelle fetch 1706.03762

# Free-text title — OpenAlex title search, Crossref fallback.
quelle fetch "The Perceptron: A Probabilistic Model" --json

# Force a network round-trip even if the cache has an entry.
quelle fetch 10.xxxx/yyyy --no-cache

# Download the PDF into the data dir (fallback chain: OpenAlex → arXiv → Unpaywall).
quelle fetch 1706.03762 --download-pdf
```

Google Scholar URLs are **not supported**: Scholar has no public API and its ToS prohibits automated access. If you only have a Scholar link, copy the paper title and feed that to `quelle fetch` — OpenAlex and Crossref together cover almost every paper with a DOI.

## Cache commands

The cache is a SQLite database keyed by DOI, arXiv id, OpenAlex id, and normalised title. A second query for the same paper is offline.

### `quelle cache stats`

Per-source counts, hit/miss ratio, total size.

```bash
quelle cache stats
quelle cache stats --json
```

### `quelle cache list`

Enumerate cached entries, newest first.

```bash
quelle cache list --limit 20
quelle cache list --json
```

### `quelle cache show`

Full cached `Publication` blob for a key.

```bash
quelle cache show 10.1109/83.902291
quelle cache show 1706.03762 --json
```

### `quelle cache clear`

Drop the cache. Prompts unless `--yes`.

```bash
quelle cache clear --yes
```

## Config and init

### `quelle init`

Bootstraps the config, data, and cache dirs and seeds a commented `.env`. Idempotent — safe to re-run.

```bash
quelle init
```

### `quelle config`

```bash
quelle config show                 # all values, API keys redacted
quelle config show --json
quelle config path                 # resolved config / data / cache paths
quelle config path --json
quelle config edit                 # open .env in $EDITOR
```

### `quelle --version`

```bash
quelle --version
```

## Exit codes

`quelle` maps errors to four exit codes:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | User error (bad identifier, unsupported input, missing required env var) |
| `2` | Source error (upstream returned an error response, paper not found) |
| `3` | Network error (timeout, DNS failure, TLS) |
| `4` | Internal error (bug) |
