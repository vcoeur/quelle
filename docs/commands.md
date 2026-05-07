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

# Bias resolution toward books for an ambiguous title (delegates to `quelle search`).
quelle fetch "Cannibal Capitalism" --type book

# Comma in the query splits title from a single-name author hint.
quelle fetch "Cannibal Capitalism, Fraser" --type book

# Explicit --author (disables the comma heuristic).
quelle fetch "Cannibal Capitalism" --author Fraser --type book

# Force a network round-trip even if the cache has an entry.
quelle fetch 10.xxxx/yyyy --no-cache

# Download the PDF into the data dir (fallback chain: OpenAlex → arXiv → Unpaywall).
quelle fetch 1706.03762 --download-pdf
```

`--type book|article|all` and `--author` only affect free-text queries — explicit DOI / ISBN / arXiv id queries always resolve directly. When set, fetch picks the top hit from `quelle search` (with the same `--type` / `--author` filters) and recurses into the regular id-based resolver to populate the full `Publication`. The title-based cache lookup is skipped in that case so a previously-cached article-on-this-title doesn't short-circuit the disambiguation.

Google Scholar URLs are **not supported**: Scholar has no public API and its ToS prohibits automated access. If you only have a Scholar link, copy the paper title and feed that to `quelle fetch` — OpenAlex and Crossref together cover almost every paper with a DOI.

## `quelle search`

Browse candidate matches across every wired open source. Use this when a free-text query is ambiguous and `quelle fetch` would commit to a single guess.

```bash
# Free-text title across all sources, top 3 results by default.
quelle search "attention is all you need"

# Comma in the query splits title from a single-name author hint.
quelle search "etranger, camus" --type book

# Explicit --author flag (disables the comma heuristic).
quelle search "etranger" --author camus --type book

# Restrict to specific sources and widen the result list.
quelle search "transformer" --limit 10 --source openalex --source arxiv --json
```

Each hit is a publication merged across the sources that returned it. Hits are merged in two passes: first by exact identifier (DOI / ISBN-13 / arXiv id), then by similarity (normalised title + first-author surname, with diacritics folded). Cross-source ranking uses Reciprocal Rank Fusion (k=60). The `id:` line on each hit is `doi:…`, `isbn:…`, or `arxiv:…` when one of those identifiers is available — copy that value back into `quelle fetch <id>` to resolve the full record.

Flags:

- `--author TEXT` — author hint, threaded into native author fields where the source supports one (OpenAlex filter, Open Library `author=`, Google Books `inauthor:`, arXiv `au:`, BnF `bib.author`); otherwise folded into the query. Setting this flag disables the comma heuristic on the positional query.
- `--type book|article|all` — restricts the source set. `all` (default) queries all six wired sources.
- `--limit INTEGER` — final merged-list size. **Default 3.**
- `--source NAME` / `--no-source NAME` — repeatable allow/deny lists. Names: `openalex`, `semantic_scholar`, `arxiv`, `open_library`, `google_books`, `bnf`.
- `--json` — emit JSON. The text-mode output is three lines per hit (rank + title, byline, id + sources) with a blank line between entries.

**Comma-split heuristic.** When `--author` is not given and the query contains a comma, the trailing piece is treated as an author hint if it is 1-3 tokens with no digits (so `"foo, smith"` splits, `"foo, 2024"` does not, `"foo, alpha beta gamma delta"` does not). The split is conservative on purpose — titles with internal commas survive as long as they do not end with a name-shaped fragment.

If a single source fails (network error, rate limit), `quelle search` logs a warning and returns the merged hits from the remaining sources rather than failing the whole call.

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
