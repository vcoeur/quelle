"""Machine-readable contract dump for `quelle schema`.

One read-only command emits the whole CLI contract so an LLM (or any
client) can self-orient without reading prose docs: every command + its
flags, the `Publication` / Source field list, the `x_vcoeur` block, a
summary of the CiteKey convention, the quelle→knoten kind map, and the
exit codes.

This module owns the *static* contract tables and reads the field lists
from the modules that own them. The command listing is introspected from
the live Typer app by `quelle.cli.introspect` and passed in by the CLI —
imports only go downward, so nothing here touches `quelle.cli`.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from quelle import __version__
from quelle.models.publication import Author, Kind, Publication
from quelle.services.citekey import KIND_MAP

# Error kinds → exit codes — mirrors `exit_code_for` in `quelle.cli._helpers`
# plus the click usage-error code set in `quelle.cli.main`. Duplicated here
# intentionally: the schema is a stable data contract and should not change
# shape just because the classifier's internals do.
EXIT_CODES: tuple[dict[str, Any], ...] = (
    {"code": 0, "meaning": "Success"},
    {"code": 1, "meaning": "User error or publication not found"},
    {"code": 2, "meaning": "Network error / upstream rate limit"},
    {"code": 3, "meaning": "Local cache (SQLite) error"},
    {"code": 4, "meaning": "Configuration error"},
    {"code": 64, "meaning": "CLI usage error (unknown flag or missing argument)"},
)

# Summary of the CiteKey convention. The authoritative rules live in
# `quelle.services.citekey`; this is a human/LLM-readable digest.
CITEKEY_RULES: dict[str, Any] = {
    "authored": "Usable author: BibTeX rule — 1=Last+Year, 2=Last1Last2+Year, 3+=Last1Al+Year.",
    "web": "No author: SiteNameYYYY[-ref]. Site from og:site_name or registrable domain "
    "(www stripped, TLD dropped, CamelCased). GitHub URLs → OrgRepo. ref = last path segment.",
    "media": "No author: ChannelYYYY[-id]. id = YouTube v=, youtu.be/<id>, or last path segment.",
    "authorless_other": "Article/book/PDF without author: CamelTitle(first 3 words)+YYYY.",
    "last_resort": "No author/site/title: RegistrableDomain+AccessDate, e.g. ExampleCom20260607.",
    "mint": "Collision: append lowercase suffix walking a,b,...,z,aa,ab,... until free.",
    "year_fallback": "Web/media: an undated source uses the retrieval year (today). "
    "Authored / authorless-other: a missing year renders as 'ND' (uppercase).",
}

X_VCOEUR_BLOCK: dict[str, str] = {
    "citekey": "Vault-ready, collision-resolved CiteKey (minted against the taken-set).",
    "vault_id": "null from quelle; knoten fills it on ingest.",
    "vault_kind": "quelle kind mapped to the knoten vault kind (see kind_map).",
    "confidence": "null from quelle; reserved for downstream confidence scoring.",
}


def _publication_fields() -> list[dict[str, str]]:
    """The Source field list + declared types, from the dataclass itself."""
    return [{"name": f.name, "type": str(f.type)} for f in fields(Publication)]


def build_schema(*, commands: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the full machine-readable contract dict for `quelle schema`.

    `commands` is the live command listing introspected by the CLI layer
    (`quelle.cli.introspect.command_listing`).
    """
    return {
        "tool": "quelle",
        "version": __version__,
        "conventions": {
            "json": "Place --json before the subcommand; the JSON shape is the stable contract.",
            "source": (
                "`quelle resolve` emits a Source: the Publication dict (snake_case) plus a "
                "top-level x_vcoeur block. `--csl` emits a CSL-JSON item instead."
            ),
            "taken_set": (
                "Feed knoten citekeys via --taken-file - (newline list or "
                '{"citekeys":[...]}) so minted keys are unique in the destination vault.'
            ),
        },
        "kinds": list(getattr(Kind, "__args__", ())),
        "kind_map": dict(KIND_MAP),
        "citekey_rules": CITEKEY_RULES,
        "x_vcoeur": X_VCOEUR_BLOCK,
        "source_fields": _publication_fields(),
        "author_fields": [{"name": f.name, "type": str(f.type)} for f in fields(Author)],
        "exit_codes": list(EXIT_CODES),
        "commands": commands,
    }
