---
title: Commands · quelle
description: Full CLI reference for quelle — fetch, search, cache, config.
---

# Commands

The CLI surface is intentionally small: four subcommands (`fetch`, `search`, `cache`, `config`) plus a top-level `--version` flag. Every command takes its output mode from a single root flag, `--json`, that must appear **before** the subcommand:

```bash
quelle --json fetch 10.1109/83.902291    # JSON
quelle fetch 10.1109/83.902291           # rich TTY rendering
```

Claude skills and shell pipelines should always pass `--json`. The first invocation of any command creates the config / data / cache directories — there is no separate `init` step.

## `quelle fetch`

Resolve a publication by DOI, arXiv id, ISBN-10/13, or free-text title. Walks the source fallback chain (OpenAlex → Crossref enrichment → Semantic Scholar → arXiv → Unpaywall for articles; Open Library → Google Books → BnF → OpenAlex for books) and returns a normalised JSON `Publication`.

```bash
# DOI — OpenAlex primary, Crossref enrichment.
quelle fetch 10.1109/83.902291
quelle --json fetch 10.1109/83.902291

# arXiv id — preprint metadata + direct PDF.
quelle fetch 1706.03762

# Free-text title — OpenAlex title search, Crossref fallback.
quelle --json fetch "The Perceptron: A Probabilistic Model"

# Bias resolution toward books for an ambiguous title (delegates to `quelle search`).
quelle fetch "Cannibal Capitalism" --book

# Comma in the query splits title from a single-name author hint.
quelle fetch "Cannibal Capitalism, Fraser" --book

# Force a network round-trip even if the cache has an entry.
quelle fetch 10.xxxx/yyyy --no-cache

# Download the PDF into the data dir (fallback chain: OpenAlex → arXiv → Unpaywall).
quelle fetch 1706.03762 --download-pdf
```

`--book` and `--article` are mutually exclusive and only affect free-text queries — explicit DOI / ISBN / arXiv id queries always resolve directly. When either flag is set, fetch picks the top hit from `quelle search` (with the same type filter) and recurses into the regular id-based resolver to populate the full `Publication`. The title-based cache lookup is skipped in that case so a previously-cached article-on-this-title doesn't short-circuit the disambiguation. The author hint comes from the comma-split heuristic on the query string itself — there is no separate `--author` flag.

Google Scholar URLs are **not supported**: Scholar has no public API and its ToS prohibits automated access. If you only have a Scholar link, copy the paper title and feed that to `quelle fetch` — OpenAlex and Crossref together cover almost every paper with a DOI.

## `quelle search`

Browse candidate matches across every wired open source. Use this when a free-text query is ambiguous and `quelle fetch` would commit to a single guess.

```bash
# Free-text title across all sources, top 3 results by default.
quelle search "attention is all you need"

# Comma in the query splits title from a single-name author hint.
quelle search "etranger, camus" --book

# Restrict to specific sources and widen the result list.
quelle --json search "transformer" --limit 10 --source openalex --source arxiv
```

Each hit is a publication merged across the sources that returned it. Hits are merged in two passes: first by exact identifier (DOI / ISBN-13 / arXiv id), then by similarity (normalised title + first-author surname, with diacritics folded). Cross-source ranking uses Reciprocal Rank Fusion (k=60). The `id:` line on each hit is `doi:…`, `isbn:…`, or `arxiv:…` when one of those identifiers is available — copy that value back into `quelle fetch <id>` to resolve the full record.

Flags:

- `--book` / `--article` — mutually exclusive. Restrict to book sources or article sources. Both absent (default) queries all wired sources.
- `--limit INTEGER` — final merged-list size. **Default 3.**
- `--source NAME` — repeatable allowlist. Names: `openalex`, `semantic_scholar`, `arxiv`, `open_library`, `google_books`, `bnf`. There is no denylist flag — pass the explicit allowlist instead.

**Comma-split heuristic.** When the query contains a comma, the trailing piece is treated as an author hint if it is 1-3 tokens with no digits (so `"foo, smith"` splits, `"foo, 2024"` does not, `"foo, alpha beta gamma delta"` does not). The author is then threaded into native author fields where the source supports one (OpenAlex filter, Open Library `author=`, Google Books `inauthor:`, arXiv `au:`, BnF `bib.author`); otherwise it is folded into the query. The split is conservative on purpose — titles with internal commas survive as long as they do not end with a name-shaped fragment. There is no separate `--author` flag: a real title that ends with a name-shaped comma fragment will be misread, so fall back to `quelle search` and pick by id.

If a single source fails (network error, rate limit), `quelle search` logs a warning and returns the merged hits from the remaining sources rather than failing the whole call.

## Cache commands

The cache is a SQLite database keyed by DOI, arXiv id, OpenAlex id, ISBN-10/13, and normalised title. A second query for the same paper is offline.

### `quelle cache list`

Header line (total + last upsert + schema version) followed by the most-recent entries.

```bash
quelle cache list --limit 20
quelle --json cache list
```

### `quelle cache show`

Full cached `Publication` blob for a DOI, arXiv id, ISBN, or exact title. Never hits the network.

```bash
quelle cache show 10.1109/83.902291
quelle --json cache show 1706.03762
```

### `quelle cache clear`

Drop the cache. Requires `--yes` — there is no interactive prompt.

```bash
quelle cache clear --yes
```

## Config commands

### `quelle config`

Bare invocation prints every effective configuration value: resolved paths, the redacted OpenAlex key, the contact / Unpaywall emails, and the User-Agent.

```bash
quelle config
quelle --json config
```

### `quelle config edit`

Open the `.env` file in `$VISUAL` / `$EDITOR` (or the OS default — `notepad` / `open` / `xdg-open`). The first time it runs, it seeds the file from the bundled template and prints a one-line "Created" hint so you know you are editing a fresh file rather than your previous edits.

```bash
quelle config edit
```

### `quelle --version`

```bash
quelle --version
```

## Behaviour

A handful of behaviours are global to the tool — surfaced here so you don't have to read the source to know about them.

**`--limit` ceilings.** `quelle search --limit N` is enforced at parse time to be `1 ≤ N ≤ 50`. Values outside that range fail before any network call. Each upstream source is then asked for `max(N * 2, 20)` candidates per request, clipped to that source's documented per-page cap (Google Books 40, Semantic Scholar 100, Open Library 100, BnF 100, OpenAlex 200, arXiv 200) so RRF has material to merge after dedup without exceeding any upstream's limit.

**Per-source rate limits enforced in-process.** A single `quelle` process serialises calls to the rate-limited sources via module-level locks:

- arXiv — 1 request per 3 s (per arXiv's published guidance).
- Unpaywall — 1 request per 100 ms.
- Google Books — 1 request per ~100 ms (~10 req/s baseline; the daily 1k cap on the unauthenticated tier is the binding limit in practice).

The locks are global to the process, so a parallel `search` call that fans out to six sources still pays the per-source cadence on the limited ones. Across processes (e.g. a shell loop calling `quelle fetch` repeatedly) every invocation re-establishes its own locks — keep your loops gentle.

**Cache size is unbounded.** The local SQLite cache has no TTL, no row-count cap, and no eviction. `cache list` surfaces total / oldest / newest / on-disk size in its header so you can decide when to `cache clear --yes`. There is no built-in `cache prune`.

**Connection pooling within one invocation.** A single `httpx.Client` is constructed per CLI run and reused for every source call, so connections are pooled within the run. Across runs every invocation re-establishes TLS — fine for single lookups, slow for tight shell loops.

## Exit codes

`quelle` maps errors to four exit codes:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | User error or paper not found (bad identifier, unsupported input) |
| `2` | Network error (timeout, DNS failure, TLS, upstream rate-limit) |
| `3` | Local cache error (corrupt SQLite file, schema-migration failure) |
| `4` | Configuration error (missing email, malformed `.env`) |
