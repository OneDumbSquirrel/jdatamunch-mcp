"""Index migration framework (A11).

When an on-disk index.json has an older index_version, registered
migrations are applied in sequence to bring it up to the current
version. Each migration is a pure function: dict -> dict, additive
where possible. Profiles missing new fields get safe defaults.

Bump INDEX_VERSION in data_store.py and register a new migration
when the on-disk schema changes incompatibly with prior versions.
"""

from __future__ import annotations

from typing import Callable


def _migrate_v1_to_v2(d: dict) -> dict:
    """v1 → v2: add quantiles / std_dev / variance / type_confidence /
    type_violation_* / semantic_type / semantic_confidence /
    cardinality_estimated / cardinality_approx to each column profile.

    All new fields are populated with safe defaults; the migration
    does NOT recompute statistics. Re-index to get accurate values.
    """
    # Dataset-level fields added in 1.x post-1.0 are optional and default-safe
    # when missing (legacy indexes load fine).
    d.setdefault("fingerprint", None)
    d.setdefault("learned_null_tokens", [])

    cols = d.get("columns", [])
    for c in cols:
        c.setdefault("std_dev", None)
        c.setdefault("variance", None)
        c.setdefault("quantiles", None)
        c.setdefault("cardinality_estimated", False)
        c.setdefault("cardinality_approx", None)
        # If old cardinality_is_exact flag is missing, infer from cardinality.
        if "cardinality_is_exact" not in c:
            c["cardinality_is_exact"] = True
        c.setdefault("type_confidence", 1.0)
        c.setdefault("type_violation_count", 0)
        c.setdefault("type_violation_samples", [])
        c.setdefault("semantic_type", None)
        c.setdefault("semantic_confidence", 0.0)
    d["index_version"] = 2
    return d


def _migrate_v2_to_v3(d: dict) -> dict:
    """v2 → v3 (1.6.0): runtime ingest tables.

    Purely additive at the JSON-index level — no per-column fields
    change. The new ``runtime_query_calls`` and ``runtime_redaction_log``
    tables live in the per-dataset SQLite store and are created on
    first ``ingest_sql_log_file`` call via
    :func:`runtime.tables.ensure_runtime_tables`. Legacy v2 indexes load
    fine; their SQLite stores simply lack the tables until first ingest.
    """
    d["index_version"] = 3
    return d


_MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
}


def migrate_to_current(d: dict) -> dict:
    """Apply migrations sequentially until index_version matches current."""
    from .data_store import INDEX_VERSION
    current = d.get("index_version", 1)
    while current < INDEX_VERSION:
        migration = _MIGRATIONS.get(current)
        if migration is None:
            # No path forward — caller will treat as "needs reindex".
            return d
        d = migration(d)
        current = d.get("index_version", current + 1)
    return d
