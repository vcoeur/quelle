"""Local SQLite cache for resolved publications.

Keyed by DOI, OpenAlex id, arXiv id, ISBN-10/13, and exact title
(last-resort). Writes store the full serialised `Publication` as a
JSON blob in the `payload_json` column so schema evolution on the
Python side doesn't require a schema migration. The structured id
columns are there so we can look up the same row from any known key.

Rows are keyed by a surrogate `id`; a work's *identity* is its
identifiers (DOI, arXiv id, ISBN-13, ISBN-10, OpenAlex id, in that
priority order). `upsert(record)` updates the existing row that shares
an identifier with the incoming record — merging away any further rows
matched through its other identifiers, since they describe the same
work — and inserts a new row otherwise. The citation key is a plain
indexed, non-unique column: re-resolving the same work under a
different key updates the old row in place, and two distinct works
that mint the same `LastnameYear` key coexist as separate rows (reads
by citation key prefer the most recently cached one). Records carrying
no identifier at all fall back to matching on citation key + exact
title, so re-resolving the same title-only work doesn't pile up rows.

Cache writes are **overwrite, not merge** at the payload level — the
matched row is replaced wholesale, by design. The resolver's
enrichment chain runs to completion before each upsert, so the new
record reflects the best information available at write time. Across
invocations, an upsert can therefore *downgrade* a previously-richer
row if the new resolution path finds less; callers that care about
strictly additive enrichment should run with the cache attached so
the chain reads the prior row first.

Raw SQL, no ORM. The schema lives as an explicit string in `_SCHEMA`
and is versioned via the `meta` table (`schema_version`).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path

# Identifier recognition is owned by `quelle._identifiers` — shared with
# the resolver so routing and cache keying can never drift apart.
from quelle._identifiers import ARXIV_ID_RE as _ARXIV_RE
from quelle._identifiers import extract_doi as _extract_doi
from quelle._identifiers import extract_isbn as _extract_isbn
from quelle.models.publication import Author, Publication
from quelle.repositories.errors import CacheError

SCHEMA_VERSION = 3

# Single definition of the publications table — shared between the
# fresh-create path (`_SCHEMA`) and the v3 recreate-and-copy migration.
_PUBLICATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS publications (
    id           INTEGER PRIMARY KEY,
    citation_key TEXT NOT NULL,
    doi          TEXT,
    openalex_id  TEXT,
    arxiv_id     TEXT,
    isbn_10      TEXT,
    isbn_13      TEXT,
    title_key    TEXT,
    payload_json TEXT NOT NULL,
    cached_at    TEXT NOT NULL
)
"""

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
{_PUBLICATIONS_TABLE};
CREATE INDEX IF NOT EXISTS publications_citation_key ON publications(citation_key);
CREATE UNIQUE INDEX IF NOT EXISTS publications_doi ON publications(doi)
    WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS publications_openalex ON publications(openalex_id)
    WHERE openalex_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS publications_arxiv ON publications(arxiv_id)
    WHERE arxiv_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS publications_isbn_13 ON publications(isbn_13)
    WHERE isbn_13 IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS publications_isbn_10 ON publications(isbn_10)
    WHERE isbn_10 IS NOT NULL;
CREATE INDEX IF NOT EXISTS publications_title ON publications(title_key);
"""


class Cache:
    """Thin SQLite wrapper. Use `Cache.open(settings)` to construct."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._db_path: Path | None = None

    @classmethod
    def open(cls, db_path: Path) -> Cache:
        """Open the cache file, creating schema on first use.

        On an existing v1 cache, runs an in-place ALTER TABLE migration
        to add the ISBN columns; on a pre-v3 cache (citation_key as
        primary key), runs a recreate-and-copy migration to the
        surrogate-id schema. Both run before stamping the version.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            _migrate_to_v2(connection)
            _migrate_to_v3(connection)
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            connection.commit()
        except sqlite3.Error as exc:
            raise CacheError(f"failed to open cache at {db_path}: {exc}") from exc
        instance = cls(connection)
        instance._db_path = db_path
        return instance

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get_by_doi(self, doi: str) -> Publication | None:
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE doi = ?",
            (doi.lower(),),
        )

    def get_by_openalex_id(self, openalex_id: str) -> Publication | None:
        """Look up by OpenAlex id in any form (`W…`, `openalex:W…`, full URL).

        New writes store the bare `W…` id, but rows written before v3
        carry the full-URL form — the query matches both.
        """
        bare = _normalize_openalex_id(openalex_id)
        if bare is None:
            return None
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE openalex_id IN (?, ?) "
            "ORDER BY cached_at DESC, id DESC LIMIT 1",
            (bare, f"https://openalex.org/{bare}"),
        )

    def get_by_citation_key(self, citation_key: str) -> Publication | None:
        """Return the cached publication for a citation key.

        Citation keys are not unique — two distinct works can mint the
        same `LastnameYear` — so this prefers the most recently cached row.
        """
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE citation_key = ? "
            "ORDER BY cached_at DESC, id DESC LIMIT 1",
            (citation_key,),
        )

    def get_by_arxiv_id(self, arxiv_id: str) -> Publication | None:
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE arxiv_id = ?",
            (arxiv_id,),
        )

    def get_by_isbn(self, isbn: str) -> Publication | None:
        """Look up a cached publication by either ISBN-10 or ISBN-13."""
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE isbn_13 = ? OR isbn_10 = ?",
            (isbn, isbn),
        )

    def get_by_title_exact(self, title: str) -> Publication | None:
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE title_key = ? "
            "ORDER BY cached_at DESC, id DESC LIMIT 1",
            (_title_key(title),),
        )

    def lookup(
        self,
        query: str,
        *,
        type_hint: str | None = None,
        author: str | None = None,
    ) -> Publication | None:
        """Try every cache lookup route for a user-supplied query string.

        Identifier-based lookups (DOI / ISBN / arXiv id / OpenAlex id)
        are always honoured — those are unambiguous. The exact-title
        fallback is skipped when `type_hint` or `author` is set, since
        a cached entry keyed by the exact title may have been resolved
        without that hint and would short-circuit the explicit
        disambiguation.
        """
        stripped = query.strip()

        isbn = _extract_isbn(stripped)
        if isbn:
            hit = self.get_by_isbn(isbn)
            if hit is not None:
                return hit

        doi = _extract_doi(stripped)
        if doi:
            hit = self.get_by_doi(doi)
            if hit is not None:
                return hit

        if _ARXIV_RE.match(stripped):
            hit = self.get_by_arxiv_id(_strip_arxiv_version(stripped))
            if hit is not None:
                return hit

        if stripped.startswith("https://openalex.org/") or stripped.startswith("openalex:"):
            hit = self.get_by_openalex_id(stripped)
            if hit is not None:
                return hit

        if type_hint is not None or author is not None:
            return None
        return self.get_by_title_exact(stripped)

    def upsert(self, publication: Publication) -> None:
        """Insert or update the cached row for `publication`.

        The target row is found by identifier (DOI, arXiv id, ISBN-13,
        ISBN-10, OpenAlex id — in that priority order) and updated in
        place; rows matched through the record's *other* identifiers
        describe the same work and are merged away, so the unique
        identifier indexes can never fire. When no identifier matches —
        including the identifier-less case, which falls back to
        citation key + exact title — a new row is inserted, so two
        distinct works sharing a citation key coexist. `cached_at` is
        refreshed on update as well as insert.
        """
        payload = json.dumps(_publication_to_dict(publication), ensure_ascii=False)
        citation_key = publication.citation_key()
        doi = (publication.doi or "").lower() or None
        openalex_id = _normalize_openalex_id(publication.openalex_id)
        title_key = _title_key(publication.title)
        values = (
            citation_key,
            doi,
            openalex_id,
            publication.arxiv_id,
            publication.isbn_10,
            publication.isbn_13,
            title_key,
            payload,
            datetime.now(UTC).isoformat(),
        )
        try:
            matched = self._matching_row_ids(
                doi=doi,
                arxiv_id=publication.arxiv_id,
                isbn_13=publication.isbn_13,
                isbn_10=publication.isbn_10,
                openalex_id=openalex_id,
                citation_key=citation_key,
                title_key=title_key,
            )
            if matched:
                target, *duplicates = matched
                if duplicates:
                    placeholders = ", ".join("?" for _ in duplicates)
                    self._conn.execute(
                        f"DELETE FROM publications WHERE id IN ({placeholders})",
                        duplicates,
                    )
                self._conn.execute(
                    """
                    UPDATE publications SET
                        citation_key = ?,
                        doi          = ?,
                        openalex_id  = ?,
                        arxiv_id     = ?,
                        isbn_10      = ?,
                        isbn_13      = ?,
                        title_key    = ?,
                        payload_json = ?,
                        cached_at    = ?
                    WHERE id = ?
                    """,
                    (*values, target),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO publications
                        (citation_key, doi, openalex_id, arxiv_id, isbn_10, isbn_13,
                         title_key, payload_json, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise CacheError(f"failed to upsert publication: {exc}") from exc

    def _matching_row_ids(
        self,
        *,
        doi: str | None,
        arxiv_id: str | None,
        isbn_13: str | None,
        isbn_10: str | None,
        openalex_id: str | None,
        citation_key: str,
        title_key: str,
    ) -> list[int]:
        """Row ids sharing any identifier with the record, priority-ordered.

        The first id is the upsert target (matched by the highest-priority
        identifier); the rest are duplicates of the same work to merge
        away. A record with no identifiers at all matches on citation
        key + title instead.
        """
        candidates = (
            ("doi", doi),
            ("arxiv_id", arxiv_id),
            ("isbn_13", isbn_13),
            ("isbn_10", isbn_10),
        )
        ordered: list[int] = []
        for column, value in candidates:
            if value is None:
                continue
            cursor = self._conn.execute(f"SELECT id FROM publications WHERE {column} = ?", (value,))
            ordered.extend(row["id"] for row in cursor.fetchall() if row["id"] not in ordered)
        if openalex_id is not None:
            # Pre-v3 rows stored the full-URL form — match both.
            cursor = self._conn.execute(
                "SELECT id FROM publications WHERE openalex_id IN (?, ?)",
                (openalex_id, f"https://openalex.org/{openalex_id}"),
            )
            ordered.extend(row["id"] for row in cursor.fetchall() if row["id"] not in ordered)
        identifiers = (doi, arxiv_id, isbn_13, isbn_10, openalex_id)
        has_identifier = any(value is not None for value in identifiers)
        if not ordered and not has_identifier:
            cursor = self._conn.execute(
                "SELECT id FROM publications WHERE citation_key = ? AND title_key = ? "
                "ORDER BY cached_at DESC, id DESC LIMIT 1",
                (citation_key, title_key),
            )
            row = cursor.fetchone()
            if row is not None:
                ordered.append(row["id"])
        return ordered

    def stats(self) -> dict[str, object]:
        """Return a small payload describing the cache: count + age + size."""
        cursor = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "MAX(cached_at) AS newest, "
            "MIN(cached_at) AS oldest "
            "FROM publications"
        )
        row = cursor.fetchone()
        size_bytes: int | None = None
        if self._db_path is not None and self._db_path.exists():
            try:
                size_bytes = self._db_path.stat().st_size
            except OSError:
                size_bytes = None
        return {
            "total": row["total"] if row else 0,
            "newest_cached_at": row["newest"] if row else None,
            "oldest_cached_at": row["oldest"] if row else None,
            "size_bytes": size_bytes,
            "schema_version": SCHEMA_VERSION,
        }

    def list_entries(self, *, limit: int = 50) -> list[dict[str, object]]:
        cursor = self._conn.execute(
            """
            SELECT citation_key, doi, title_key, cached_at
            FROM publications
            ORDER BY cached_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "citation_key": row["citation_key"],
                "doi": row["doi"],
                "title_key": row["title_key"],
                "cached_at": row["cached_at"],
            }
            for row in cursor.fetchall()
        ]

    def clear(self) -> int:
        """Delete every row. Returns the number of rows removed."""
        cursor = self._conn.execute("DELETE FROM publications")
        self._conn.commit()
        return cursor.rowcount or 0

    def _fetch_one(self, sql: str, params: tuple) -> Publication | None:
        try:
            cursor = self._conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise CacheError(f"cache lookup failed: {exc}") from exc
        row = cursor.fetchone()
        if row is None:
            return None
        return _publication_from_payload(row["payload_json"])


def _migrate_to_v2(connection: sqlite3.Connection) -> None:
    """Add `isbn_10` and `isbn_13` columns to a pre-v2 publications table.

    No-op when the table doesn't exist yet (first-run case — `_SCHEMA`
    will create it with the new columns directly) or when the columns
    are already present (idempotent re-run).
    """
    cursor = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='publications'"
    )
    if cursor.fetchone() is None:
        return
    existing = {row[1] for row in connection.execute("PRAGMA table_info(publications)")}
    if "isbn_10" not in existing:
        connection.execute("ALTER TABLE publications ADD COLUMN isbn_10 TEXT")
    if "isbn_13" not in existing:
        connection.execute("ALTER TABLE publications ADD COLUMN isbn_13 TEXT")


def _migrate_to_v3(connection: sqlite3.Connection) -> None:
    """Rebuild a pre-v3 publications table around a surrogate-id key.

    v2 keyed rows by `citation_key TEXT PRIMARY KEY`; v3 uses
    `id INTEGER PRIMARY KEY` with citation_key as a plain indexed
    column. SQLite can't drop a primary key in place, so this is a
    recreate-and-copy: rename the old table, create the new one, copy
    every row, drop the old table (its indexes go with it — `_SCHEMA`
    recreates them on the new table). OpenAlex ids are normalised from
    the full-URL form to the bare `W…` id while we're at it.

    No-op when the table doesn't exist yet (first-run case) or when
    the `id` column is already present (idempotent re-run). Runs after
    `_migrate_to_v2`, so the ISBN columns are always present here.
    """
    cursor = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='publications'"
    )
    if cursor.fetchone() is None:
        return
    existing = {row[1] for row in connection.execute("PRAGMA table_info(publications)")}
    if "id" in existing:
        return
    connection.execute("ALTER TABLE publications RENAME TO publications_pre_v3")
    connection.execute(_PUBLICATIONS_TABLE)
    connection.execute(
        """
        INSERT INTO publications
            (citation_key, doi, openalex_id, arxiv_id, isbn_10, isbn_13,
             title_key, payload_json, cached_at)
        SELECT citation_key, doi, openalex_id, arxiv_id, isbn_10, isbn_13,
               title_key, payload_json, cached_at
        FROM publications_pre_v3
        """
    )
    connection.execute("DROP TABLE publications_pre_v3")
    connection.execute(
        "UPDATE publications "
        "SET openalex_id = replace(openalex_id, 'https://openalex.org/', '') "
        "WHERE openalex_id LIKE 'https://openalex.org/%'"
    )


def _publication_to_dict(publication: Publication) -> dict:
    """Serialise a Publication into a JSON-safe dict."""
    return asdict(publication)


def _publication_from_payload(payload_json: str) -> Publication:
    """Deserialise a JSON blob into a Publication."""
    data = json.loads(payload_json)
    authors = [Author(**author) for author in data.get("authors") or []]
    known = {f.name for f in fields(Publication)}
    filtered = {k: v for k, v in data.items() if k in known}
    filtered["authors"] = authors
    return Publication(**filtered)


def _normalize_openalex_id(value: str | None) -> str | None:
    """Reduce any accepted OpenAlex id form to the bare `W…` id.

    Accepts `W…`, `openalex:W…`, and the full-URL form the OpenAlex
    mapper produces (`https://openalex.org/W…`).
    """
    if not value:
        return None
    cleaned = value.strip()
    cleaned = cleaned.removeprefix("openalex:")
    cleaned = cleaned.removeprefix("https://openalex.org/")
    cleaned = cleaned.removeprefix("http://openalex.org/")
    return cleaned or None


def _title_key(title: str) -> str:
    """Lowercased, whitespace-collapsed title for title_key column."""
    return " ".join((title or "").split()).lower()


def _strip_arxiv_version(arxiv_id: str) -> str:
    """Drop the `vN` suffix if present. `1706.03762v5` -> `1706.03762`.

    Local copy of `arxiv._strip_version` so the cache module does not
    import from the source-adapter layer (services / cache live in the
    same tier; sources are higher up the dependency graph).
    """
    cleaned = arxiv_id.strip().lower()
    if "v" in cleaned:
        head, sep, tail = cleaned.rpartition("v")
        if sep and tail.isdigit():
            return head
    return cleaned
