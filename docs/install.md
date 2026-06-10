---
title: Install · quelle
description: How to install quelle, bootstrap the config, and configure API keys for OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall, Open Library, Google Books, and BnF.
---

# Install

## From PyPI

```bash
pipx install quelle
# or: uv tool install quelle
```

Both install `quelle` into its own isolated venv and put it on your `$PATH`.

## First run

The first invocation of any subcommand creates the config, data, and cache directories. To seed a commented `.env` and open it in your editor:

```bash
quelle config edit                 # creates .env on first run, then opens it in $EDITOR
```

The only variable worth setting by default is `QUELLE_CONTACT_EMAIL` — it goes into the `User-Agent` header and enrolls you in the Crossref and OpenAlex polite pools. Don't fake it: Unpaywall requires a real contact, and OpenAlex (as of January 2026) is transitioning to key-based authentication where the `OPENALEX_API_KEY` variable is used instead.

First fetch:

```bash
quelle fetch 10.1109/83.902291
quelle fetch 1706.03762 --download-pdf
quelle --json fetch "The Perceptron"
```

Verify the configuration at any time:

```bash
quelle --help
quelle --json config
```

## Cross-OS paths

`quelle` follows each OS's standard "config dir + data dir + cache dir" layout via [`platformdirs`](https://platformdirs.readthedocs.io/):

| Role | Linux (XDG) | macOS | Windows |
|---|---|---|---|
| Config (`.env`) | `~/.config/quelle/` | `~/Library/Application Support/quelle/` | `%APPDATA%\quelle\` |
| Data (downloaded PDFs) | `~/.local/share/quelle/` | `~/Library/Application Support/quelle/` | `%LOCALAPPDATA%\quelle\` |
| Cache (SQLite index) | `~/.cache/quelle/` | `~/Library/Caches/quelle/` | `%LOCALAPPDATA%\quelle\Cache\` |

Any of the three can be overridden via env vars — useful for tests, Docker, or custom deployments:

```bash
export QUELLE_CONFIG_DIR=/etc/quelle
export QUELLE_DATA_DIR=/srv/quelle/data
export QUELLE_CACHE_DIR=/var/cache/quelle
```

Inspect the resolved paths and effective config:

```bash
quelle config                      # all values including resolved paths and redacted API key
quelle --json config               # same payload as JSON, scriptable
```

## API keys and the polite pool

`quelle` works out of the box with no keys, but setting `QUELLE_CONTACT_EMAIL` is strongly recommended. Optional extras:

| Variable | Source | When to set |
|---|---|---|
| `QUELLE_CONTACT_EMAIL` | OpenAlex, Crossref, Unpaywall | Always — enrolls you in the polite pools and is mandatory for Unpaywall. |
| `OPENALEX_API_KEY` | OpenAlex | Required once OpenAlex finishes deprecating the mailto polite pool (Jan 2026 rollout). |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar | Optional — boosts unauth rate limit. Free key from Ai2. |
| `GOOGLE_BOOKS_API_KEY` | Google Books | Optional — raises the 1 000 requests/day-per-IP unauthenticated cap. |
| `QUELLE_USER_AGENT` | all HTTP requests | Optional override. Defaults to `quelle/<version>`, with `(+mailto:…)` appended when the contact email is set. |

See [`.env.example`](https://github.com/vcoeur/quelle/blob/main/.env.example) for the full list.

## Development from a source checkout

```bash
git clone https://github.com/vcoeur/quelle.git
cd quelle
make dev-install                   # uv sync --all-groups
cp .env.example .env               # optional — sets contact email for the polite pool
uv run quelle --help               # run the CLI straight from the repo
uv run quelle --json config
make test                          # pytest + pytest-httpx
make lint                          # ruff check + format --check
make format                        # ruff --fix + format
```

When run from the repo, `quelle` picks up the `.env` at the repo root, but downloaded PDFs and the SQLite cache go into a repo-local `.dev-state/` directory so your installed user data stays clean.
