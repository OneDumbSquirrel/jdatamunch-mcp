"""get_redaction_log — forensic accounting of PII redactions (v1.10.0).

Reads the ``runtime_redaction_log`` table populated by ``ingest_sql_log``
(v1.6.0) and surfaces per-pattern counts so operators can verify the
redaction chokepoint is firing on production traffic — *before* trusting
that "yes, my SQL string literals were stripped before they hit disk."

The redaction patterns come from :mod:`jdatamunch_mcp.redact`. Common
labels seen in practice:

  * ``sql_string_literal``  — quoted SQL literals (``WHERE x = 'foo'``)
  * ``sql_numeric_literal`` — bare numbers in WHERE/IN/BETWEEN clauses
  * ``email``               — RFC-shaped emails
  * ``credit_card``         — Luhn-validated card numbers
  * ``ssn``                 — US SSNs (SSA-rule)
  * ``jwt`` / ``aws_access_key`` / ``github_pat`` / ``slack_token`` /
    ``private_key`` / ``api_key_prefixed`` / ``api_key_openai`` — secrets
  * ``ipv4_address``        — IPv4

Mirrors jcm's :mod:`jcodemunch_mcp.tools.get_redaction_log` but keyed on
``dataset_id`` rather than ``owner/name``, and reads the jData table
schema ``(pattern, count, source, last_seen)`` rather than jcm's
``(pattern, redaction_count, source, last_redacted)``.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..storage.data_store import DataStore
from ..runtime.tables import ensure_runtime_tables


_VALID_SOURCES = ("sql_log",)


def get_redaction_log(
    dataset_id: str,
    source: Optional[str] = None,
    *,
    since_days: int = 30,
    storage_path: Optional[str] = None,
) -> dict:
    """Return per-pattern redaction counts for a dataset.

    Args:
        dataset_id: The indexed dataset whose runtime_redaction_log to read.
        source: Optional filter to a single source label. Today only
            ``sql_log`` is populated by ``ingest_sql_log``. Default
            ``None`` returns all sources present.
        since_days: Lookback window for ``last_seen`` filtering. Patterns
            last fired before the cutoff are omitted. Default 30.
        storage_path: Custom data-index root.

    Returns:
        ``{
            'dataset': dataset_id,
            'sources': [<source>, ...],
            'since_iso': cutoff,
            'patterns': [{'source', 'pattern', 'count', 'last_seen'}, ...],
            'total_redactions': N,
            '_meta': {...},
        }`` on success.

        ``{error, reason, hint, _meta}`` on refusal (dataset missing or
        no runtime data ingested).
    """
    t0 = time.perf_counter()
    if source is not None and source not in _VALID_SOURCES:
        return {
            "error": f"unknown source {source!r}; valid: {list(_VALID_SOURCES)}",
            "reason": "invalid_source",
        }
    since_days = max(1, int(since_days))

    store = DataStore(base_path=storage_path)
    idx = store.load(dataset_id)
    if idx is None:
        return {
            "error": f"Dataset not indexed: {dataset_id}",
            "reason": "dataset_not_found",
            "hint": "Run index_local first, then ingest_sql_log to populate runtime data.",
        }

    db_path = store.sqlite_path(dataset_id)
    if not db_path.exists():
        return {
            "error": f"Dataset SQLite missing: {dataset_id}",
            "reason": "dataset_not_found",
            "hint": "Run index_local first, then ingest_sql_log to populate runtime data.",
        }

    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=since_days)).isoformat()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_runtime_tables(conn)
        try:
            base_sql = (
                "SELECT source, pattern, count, last_seen "
                "FROM runtime_redaction_log WHERE last_seen >= ?"
            )
            params: list = [cutoff]
            if source is not None:
                base_sql += " AND source = ?"
                params.append(source)
            base_sql += " ORDER BY count DESC, source ASC, pattern ASC"
            rows = conn.execute(base_sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        conn.close()

    patterns = [
        {
            "source": r["source"],
            "pattern": r["pattern"],
            "count": int(r["count"] or 0),
            "last_seen": r["last_seen"] or "",
        }
        for r in rows
    ]
    sources_seen = sorted({p["source"] for p in patterns})
    total = sum(p["count"] for p in patterns)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "dataset": dataset_id,
        "sources": sources_seen,
        "since_iso": cutoff,
        "patterns": patterns,
        "total_redactions": total,
        "_meta": {
            "timing_ms": elapsed_ms,
            "filter_source": source or "(all)",
            "since_days": since_days,
            "tip": (
                "Empty patterns list = no redactions recorded in the window. "
                "If you expected hits and don't see any, verify that "
                "ingest_sql_log ran with redact=True (the default) and that "
                "queries actually contained the patterns you expect to be "
                "stripped (literals, emails, secrets)."
            ),
        },
    }
