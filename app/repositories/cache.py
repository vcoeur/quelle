"""Local SQLite cache for resolved publications.

Keyed by DOI, OpenAlex id, arXiv id, and exact title (last-resort).
Writes store the full serialised `Publication` as a JSON blob in the
`payload_json` column so schema evolution on the Python side doesn't
require a schema migration. The structured id columns are there so
we can look up the same row from any known key.

Same conventions as KastenManager's `Store`: raw SQL, explicit
schema in `_SCHEMA`, stampable `schema_version` in a `meta` table.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path

from app.models.publication import Author, Publication
from app.repositories.errors import CacheError

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
    citation_key TEXT PRIMARY KEY,
    doi          TEXT,
    openalex_id  TEXT,
    arxiv_id     TEXT,
    title_key    TEXT,
    payload_json TEXT NOT NULL,
    cached_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS publications_doi ON publications(doi)
    WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS publications_openalex ON publications(openalex_id)
    WHERE openalex_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS publications_arxiv ON publications(arxiv_id)
    WHERE arxiv_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS publications_title ON publications(title_key);
"""


class Cache:
    """Thin SQLite wrapper. Use `Cache.open(settings)` to construct."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    @classmethod
    def open(cls, db_path: Path) -> Cache:
        """Open the cache file, creating schema on first use."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            connection.commit()
        except sqlite3.Error as exc:
            raise CacheError(f"failed to open cache at {db_path}: {exc}") from exc
        return cls(connection)

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
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE openalex_id = ?",
            (openalex_id,),
        )

    def get_by_arxiv_id(self, arxiv_id: str) -> Publication | None:
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE arxiv_id = ?",
            (arxiv_id,),
        )

    def get_by_title_exact(self, title: str) -> Publication | None:
        return self._fetch_one(
            "SELECT payload_json FROM publications WHERE title_key = ?",
            (_title_key(title),),
        )

    def upsert(self, publication: Publication) -> None:
        """Insert or replace the row for `publication.citation_key()`."""
        payload = json.dumps(_publication_to_dict(publication), ensure_ascii=False)
        try:
            self._conn.execute(
                """
                INSERT INTO publications
                    (citation_key, doi, openalex_id, arxiv_id, title_key, payload_json, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(citation_key) DO UPDATE SET
                    doi          = excluded.doi,
                    openalex_id  = excluded.openalex_id,
                    arxiv_id     = excluded.arxiv_id,
                    title_key    = excluded.title_key,
                    payload_json = excluded.payload_json,
                    cached_at    = excluded.cached_at
                """,
                (
                    publication.citation_key(),
                    (publication.doi or "").lower() or None,
                    publication.openalex_id,
                    publication.arxiv_id,
                    _title_key(publication.title),
                    payload,
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise CacheError(f"failed to upsert publication: {exc}") from exc

    def stats(self) -> dict[str, object]:
        """Return a small payload with the total count and newest entry."""
        cursor = self._conn.execute(
            "SELECT COUNT(*) AS total, MAX(cached_at) AS newest FROM publications"
        )
        row = cursor.fetchone()
        return {
            "total": row["total"] if row else 0,
            "newest_cached_at": row["newest"] if row else None,
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


def _title_key(title: str) -> str:
    """Lowercased, whitespace-collapsed title for title_key column."""
    return " ".join((title or "").split()).lower()
