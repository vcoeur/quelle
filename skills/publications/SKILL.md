---
name: publications
description: Fetch academic publication metadata (title, authors, year, DOI, abstract, citation count) and optionally download the open-access PDF from a DOI, arXiv id, or free-text title. Walks a fallback chain of free open APIs — OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall — and returns a normalised JSON blob. Has no knowledge of any particular note-taking system; use this as a building block that you pipe into your own downstream workflow (reference manager import, note template, citation generator, etc). Use when the user says "look up this paper", "get metadata for DOI 10.xxx", "find the arXiv entry for X", or pastes a paper identifier with intent to retrieve information about it. Google Scholar URLs are not supported — ask the user to copy the paper title from Scholar and pass it as free text instead.
argument-hint: "<DOI | arXiv id | title>"
allowed-tools: Read, Write, Edit, Bash(publications fetch:*), Bash(publications cache:*), Bash(publications config:*), Bash(publications version:*), Bash(python3:*), Bash(jq:*), Bash(command -v publications:*)
---

Thin wrapper around the `publications` CLI that ships with PublicationManager. Given an identifier or title, resolve the paper through free open APIs and print its normalised JSON record.

**This is a minimal example** — downstream workflows (saving the result to a Zettelkasten, appending to a BibTeX file, creating a reference-manager entry, etc.) are deliberately out of scope. Adapt the last step for your own pipeline.

## Prerequisites

```bash
command -v publications
```

If missing:

```bash
cd <path-to-PublicationManager>
make tool-install
```

Set at least `PUBLICATIONS_CONTACT_EMAIL` in a `.env` file so you join the Crossref / OpenAlex polite pool (see `.env.example`).

## Request

> $ARGUMENTS

## Run it

Call `publications fetch` with `--json` and parse the result:

```bash
publications fetch "$ARGUMENTS" --json > /tmp/paper.json
```

Add `--download-pdf` if you also want the OA PDF saved to `$PUBLICATIONS_HOME/.publications-state/pdfs/<CitationKey>.pdf`:

```bash
publications fetch "$ARGUMENTS" --download-pdf --json > /tmp/paper.json
```

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

Exit codes you should surface to the user on failure:

- `1` — user error / paper not found
- `2` — network or upstream API error (rate limited, timeout, 5xx)
- `3` — local cache error
- `4` — configuration error (missing email, bad `.env`)

On a non-zero exit, print the stderr from `publications fetch` verbatim and stop — don't try to recover.

## Inspecting the cache

Repeated lookups are served from a local SQLite cache (no network). Useful commands:

```bash
publications cache stats          # total + last upsert + schema version
publications cache list           # most-recent cached papers
publications cache show 10.xxx/y  # offline lookup by DOI / arXiv id / title
publications cache clear --yes    # wipe everything (irreversible)
```

## Cleanup

```bash
rm -f /tmp/paper.json
```

## Adapting this skill to your workflow

This skill stops at "print the metadata". If you want to do something with the result — save to Obsidian, append to a BibTeX file, create a Roam block, email yourself the PDF — fork this SKILL.md and add your own step after `publications fetch` returns. The JSON shape is stable: `title`, `authors` (list of `{name, orcid?, affiliation?}`), `year`, `doi`, `arxiv_id`, `openalex_id`, `abstract`, `citation_count`, `is_open_access`, `pdf_url`, `local_pdf_path`, `venue`, `publisher`, `journal_volume`, `journal_issue`, `page_range`, `topics`, `source_url`, `citation_key`, `resolved_from_chain`.

## Installation

Drop this `SKILL.md` into either:

- `~/.claude/skills/publications/SKILL.md` — available in every Claude Code session
- `<project>/.claude/skills/publications/SKILL.md` — project-local (auto-loaded when Claude Code opens that project)

See [Claude Code's skill documentation](https://docs.claude.com/en/docs/claude-code/skills) for details.
