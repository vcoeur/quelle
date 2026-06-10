---
name: quelle
description: Resolve ANY source — a DOI / arXiv id / ISBN, a free-text title, an http(s) URL (web page, video), or a local PDF — into a normalised bibliographic record and a vault-ready CiteKey, via the `quelle` CLI. Emits a JSON "Source" (the publication dict plus an x_vcoeur block) or a CSL-JSON item. Use whenever you need to identify, normalise, or cite a source, or mint a stable key for it. This is the CLI contract; vault conventions live in a separate skill.
argument-hint: "<DOI | arXiv id | ISBN | title | URL | path/to.pdf>"
allowed-tools: Read, Bash(quelle:*), Bash(knoten:*), Bash(python3:*), Bash(jq:*), Bash(command -v quelle:*)
---

Thin, **convention-free** wrapper around the [`quelle`](https://github.com/vcoeur/quelle) CLI. It encodes how to drive the tool *correctly* — JSON parsing, routing, CiteKey minting, error handling. It encodes **no vault conventions** (which notes get created, where, with what frontmatter). Those belong in a separate, user-specific skill (e.g. the private `/kasten` skill) that references this one. The authoritative, never-drifting contract is `quelle schema --json`.

## Use at your own risk

`quelle` is MIT software, **as-is, no warranty**. It queries free public APIs (OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall, Open Library, Google Books, BnF) and fetches arbitrary URLs. **You are responsible for each upstream's terms of service** — no bulk scraping, no rehosting downloaded PDFs, no in-copyright book downloads. Verify critical metadata against the canonical source.

## Request

> $ARGUMENTS

---

## 1 — Prerequisites

```bash
command -v quelle || pipx install quelle   # or: uv tool install quelle
```

First invocation creates the config / data / cache dirs. Set `QUELLE_CONTACT_EMAIL` (`quelle config edit`) to join the Crossref / OpenAlex polite pool. If `quelle` is not on PATH, say so and stop.

## 2 — Always pass `--json` (before the subcommand)

```bash
quelle --json resolve "<input>"
```

`--json` is a **root** flag — it goes before the subcommand, not after. The JSON shape is the stable, parseable contract; the TTY rendering is for humans. Parse with `jq` or `python3`.

## 3 — Discover the contract: `quelle schema --json`

```bash
quelle schema --json
```

Returns every command + its flags, the Source field list + types, the `x_vcoeur` block, a summary of the CiteKey convention, the quelle→knoten kind map, and the exit codes. Read it once to self-orient; do not hard-code the surface from this file.

## 4 — `quelle resolve` — the universal entry

`resolve` accepts **anything** and always returns a Source:

| Input | Route |
|---|---|
| `path/to/file.pdf` (exists) | local PDF metadata (degrades to filename + mtime year) |
| `https://…` URL | embedded DOI/arXiv → rich resolver; else web/media page (Open Graph metadata) |
| DOI / ISBN / arXiv id | rich academic/book resolver + enrichment |
| free text | OpenAlex / multi-source search |

```bash
quelle --json resolve 10.1109/83.902291
quelle --json resolve https://bambulab.com/en/x1
quelle --json resolve https://www.youtube.com/watch?v=dQw4w9WgXcQ
quelle --json resolve ./paper.pdf
quelle --json resolve "attention is all you need"
```

Flags: `--book` / `--article` (bias free-text only, mutually exclusive), `--no-cache`, `--download-pdf` / `-d` (OA / public-domain only), `--taken` / `--taken-file` (see §6), `--csl` (see §7).

## 5 — The Source shape (`x_vcoeur`)

`resolve` emits the Publication dict (snake_case) plus a top-level `x_vcoeur` block:

```json
{
  "title": "...", "authors": [{"name": "...", "orcid": null, "affiliation": null}],
  "year": 2017, "doi": "...", "kind": "article", "source_url": null,
  "citation_key": "Vaswani2017",
  "x_vcoeur": {"citekey": "Vaswani2017", "vault_id": null, "vault_kind": "article", "confidence": null}
}
```

- `citation_key` — the BibTeX-style base key (always present).
- `x_vcoeur.citekey` — the **vault-ready, collision-resolved** CiteKey (see §6). Use this as the vault key.
- `x_vcoeur.vault_kind` — the quelle `kind` mapped to a knoten vault kind (article/book/web/media/document).
- `vault_id` / `confidence` — `null` from quelle; a downstream consumer (knoten) fills them.

knoten's `reference --from-source` consumes exactly this Source object.

## 6 — Mint a vault-unique CiteKey (`--taken` / `--taken-file`)

quelle stays decoupled from the vault: you **inject** the set of CiteKeys already in use, and quelle disambiguates against it (collision → lowercase suffix `a`, `b`, …). Feed it the keys knoten already holds:

```bash
knoten citekeys --json | quelle --json resolve "<input>" --taken-file -
```

`--taken-file` accepts a newline-delimited list, the `knoten citekeys --json` object (`{"citekeys":[...]}`), or `-` for stdin. `--taken K1,K2` adds a few inline. The minted key lands in `x_vcoeur.citekey`; `citation_key` stays the un-disambiguated base.

## 7 — CSL-JSON export (`--csl`)

```bash
quelle --json resolve 10.1109/83.902291 --csl
```

Emits one CSL-JSON item (`id` = CiteKey, `type`, `title`, `author` as `[{family, given}]`, `issued.date-parts`, `container-title`, `DOI`, `ISBN`, `URL`) for a reference manager or `citeproc`. Export only — not the vault Source.

## 8 — Other commands

| Command | Purpose |
|---|---|
| `quelle --json fetch <id-or-title>` | Resolve a single academic/book record (no CiteKey minting). |
| `quelle --json search <query>` | List candidate hits across sources; copy an `id:` back into `fetch`/`resolve`. |
| `quelle --json cache {list,show,clear}` | Inspect / wipe the SQLite cache. |
| `quelle --json config` | Show effective config; `quelle config edit` opens the `.env`. |

## 9 — Errors + exit codes

On failure, a structured error goes to **stderr** and the process exits non-zero:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | User error / not found |
| 2 | Network error / upstream rate limit |
| 3 | Local cache error |
| 4 | Configuration error |
| 64 | CLI usage error (unknown flag or missing argument) |

Branch on the exit code, not on the message text. Google Scholar URLs are unsupported (code 1) — copy the title and retry as free text.

---

## Installation

```bash
quelle skill install --user        # -> ~/.config/agents/skills/quelle/SKILL.md
quelle skill install --project     # -> <cwd>/.agents/skills/quelle/SKILL.md
quelle skill install --claude      # -> ~/.claude/skills/quelle/SKILL.md
quelle skill status                # where it's installed + whether it matches the bundled copy
```

To add vault conventions (where notes go, frontmatter, ingest flow), fork into a **separate** skill that references this one for mechanics — keep this file convention-free so it updates cleanly with the tool.

$ARGUMENTS
