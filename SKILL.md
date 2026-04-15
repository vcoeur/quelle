---
name: quelle
description: Fetch academic publication metadata (title, authors, year, DOI, abstract, citation count) and optionally the open-access PDF, given a DOI, arXiv id, or free-text title. Walks a fallback chain of free open APIs — OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall — and returns a normalised JSON blob. Has no knowledge of any particular note-taking system. Use when the user says "look up this paper", "get metadata for DOI 10.xxx", "find the arXiv entry for X", or pastes a paper identifier with intent to retrieve information about it. Google Scholar URLs are not supported — ask the user to copy the paper title from Scholar and pass it as free text instead.
argument-hint: "<DOI | arXiv id | title>"
allowed-tools: Read, Write, Edit, Bash(quelle fetch:*), Bash(quelle cache:*), Bash(quelle config:*), Bash(quelle init:*), Bash(quelle version:*), Bash(python3:*), Bash(jq:*), Bash(command -v quelle:*)
---

Thin wrapper around the [`quelle`](https://github.com/vcoeur/quelle) CLI. Given an identifier or title, resolve the paper through free open APIs and print its normalised JSON record.

**This is a minimal example skill** — downstream workflows (saving the result to a zettelkasten, appending to a BibTeX file, creating a reference-manager entry, etc.) are deliberately out of scope. Adapt the last step for your own pipeline.

## Use at your own risk

`quelle` is MIT-licensed software provided **as-is, with no warranty**. Its JSON output may be incomplete, out of date, or wrong — always verify critical metadata against the canonical upstream source before relying on it.

## Responsible API usage — read this first

`quelle` queries free, public APIs (OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall) on your behalf. Each has its own Terms of Service, licence, and rate limits. **You are responsible for complying with every upstream's terms.** The MIT licence on `quelle` covers the *code* of the tool — **not** the data you fetch through it.

This skill is intended for **one-off, personal, academic research lookups** — a few papers at a time. It is **not** suitable for:

- **Bulk scraping or batch ingestion** of many records. Most upstreams publish free database snapshots (OpenAlex, Unpaywall, Semantic Scholar) — use those instead of hammering the live API. Running this skill in a loop over a long list of DOIs is misuse.
- **Rehosting downloaded PDFs** on a public server. `--download-pdf` writes to a local cache on your machine, which is fine. Re-serving arXiv, publisher, or any copyrighted full-text from your own infrastructure is not (see arXiv's Terms of Use in particular).
- **Commercial repackaging** of the JSON output as a paid product. Some Semantic Scholar records are `CC BY-NC`; attribution is always required.

Before using `quelle` at any non-trivial scale, read each upstream's terms:

- [OpenAlex](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication) — CC0 data, ~100k / day on the polite pool. Set `QUELLE_CONTACT_EMAIL` in your `.env`.
- [Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) — CC0 metadata, polite-pool throttling.
- [arXiv API](https://info.arxiv.org/help/api/tou.html) — **1 request / 3 seconds** (the tool enforces this globally). Metadata is CC0, PDFs keep their authors' licence.
- [Semantic Scholar](https://www.semanticscholar.org/product/api/license) — attribution **required**. The API is "AS IS" with no warranty and may not be repackaged or resold.
- [Unpaywall](https://unpaywall.org/products/api) — 100k req/day, email parameter mandatory. For bulk workloads download the free snapshot instead.

**Google Scholar is not supported.** Google Scholar has no official API, and Google's Terms of Service prohibit automated access. Passing a Scholar URL to `quelle fetch` returns a `UserError` asking you to copy the paper title manually and retry.

## Prerequisites

```bash
command -v quelle
```

If missing, install from PyPI:

```bash
pipx install quelle
# or, fully isolated:
uv tool install quelle
```

First-time bootstrap creates the config, data, and cache dirs and seeds a default `.env`:

```bash
quelle init
quelle config edit   # fill in QUELLE_CONTACT_EMAIL
```

`QUELLE_CONTACT_EMAIL` goes into the User-Agent and enrolls you in the Crossref / OpenAlex polite pool. **Do not fake the address** — Unpaywall uses it to contact you if something goes wrong. Use your real email.

## Request

> $ARGUMENTS

## Run it

Call `quelle fetch` with `--json` and parse the result:

```bash
quelle fetch "$ARGUMENTS" --json > /tmp/paper.json
```

Add `--download-pdf` if you also want the OA PDF saved under the local data dir (respecting the upstream per-source rate limits):

```bash
quelle fetch "$ARGUMENTS" --download-pdf --json > /tmp/paper.json
```

`quelle config path` prints the exact data-dir location where PDFs land (OS-dependent via `platformdirs`).

Then show the key fields to the user:

```bash
python3 - <<'PY'
import json
d = json.load(open("/tmp/paper.json"))
print(f"Title:     {d['title']}")
print(f"Year:      {d.get('year')}")
print(f"DOI:       {d.get('doi')}")
print(f"arXiv:     {d.get('arxiv_id')}")
print(f"Authors:   {', '.join(a['name'] for a in (d.get('authors') or []))}")
print(f"Citekey:   {d.get('citation_key')}")
print(f"Chain:     {', '.join(d.get('resolved_from_chain') or [])}")
print(f"PDF URL:   {d.get('pdf_url') or '(none)'}")
print(f"Local PDF: {d.get('local_pdf_path') or '(not downloaded)'}")
if d.get("abstract"):
    print()
    print("Abstract:")
    print(d["abstract"])
PY
```

Exit codes to surface to the user on failure:

- `1` — user error / paper not found
- `2` — network or upstream API error (rate limited, timeout, 5xx)
- `3` — local cache error
- `4` — configuration error (missing email, bad `.env`)

On a non-zero exit, print the stderr from `quelle fetch` verbatim and stop — do not retry in a loop. If the error is `2` (rate limit), wait and ask the user whether to try again later; do not silently back off and keep hitting the API.

## Inspecting the local cache

Repeated lookups for the same identifier are served from a local SQLite cache — no network. Useful commands:

```bash
quelle cache stats          # total + last upsert + schema version
quelle cache list           # most-recent cached papers
quelle cache show 10.xxx/y  # offline lookup by DOI / arXiv id / title
quelle cache clear --yes    # wipe everything (irreversible)
```

Prefer `quelle cache show` over `quelle fetch` when you are only refreshing the view of an already-known paper — it keeps traffic off the upstream APIs entirely.

## Cleanup

```bash
rm -f /tmp/paper.json
```

## Adapting this skill to your workflow

This skill stops at "print the metadata". If you want to do something with the result — save to Obsidian, append to a BibTeX file, create a Zotero entry, email yourself the PDF — fork this `SKILL.md` and add your own step after `quelle fetch` returns. The JSON shape is stable: `title`, `authors` (list of `{name, orcid?, affiliation?}`), `year`, `doi`, `arxiv_id`, `openalex_id`, `abstract`, `citation_count`, `is_open_access`, `pdf_url`, `local_pdf_path`, `venue`, `publisher`, `journal_volume`, `journal_issue`, `page_range`, `topics`, `source_url`, `citation_key`, `resolved_from_chain`.

When designing that downstream step, keep the responsible-use principles from the top of this file in mind — in particular, do not let your workflow turn `quelle` into a batch pipeline that calls the upstream APIs hundreds of times in a row.

## Installation

Drop this `SKILL.md` into either:

- `~/.claude/skills/quelle/SKILL.md` — available in every Claude Code session
- `<project>/.claude/skills/quelle/SKILL.md` — project-local (auto-loaded when Claude Code opens that project)

See [Claude Code's skill documentation](https://docs.claude.com/en/docs/claude-code/skills) for details.

$ARGUMENTS
