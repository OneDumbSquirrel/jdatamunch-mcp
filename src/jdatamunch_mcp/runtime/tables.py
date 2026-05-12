"""SQLite schema for the runtime trace tables (v1.6.0).

Two tables live alongside the dataset's row store in
``~/.data-index/{dataset_id}/data.sqlite``:

* ``runtime_query_calls`` — per (query_fingerprint, table_ref, column_ref)
  rollup. Upsert-on-conflict semantics: a fingerprint hit a second time
  increments ``calls`` and refreshes ``last_seen`` rather than appending.
* ``runtime_redaction_log`` — per-pattern counts so operators can verify
  the chokepoint actually fires on production traffic.

Tables are idempotent — :func:`ensure_runtime_tables` runs
``CREATE TABLE IF NOT EXISTS`` on every ingest and on the v2→v3
migration, so legacy datasets gain empty tables on first touch.
"""

from __future__ import annotations

import sqlite3
from typing import Mapping


RUNTIME_TABLE_SCHEMAS: Mapping[str, str] = {
    "runtime_query_calls": """
        CREATE TABLE IF NOT EXISTS runtime_query_calls (
            query_fingerprint TEXT NOT NULL,
            table_ref TEXT,
            column_ref TEXT,
            calls INTEGER NOT NULL DEFAULT 0,
            total_time_ms REAL NOT NULL DEFAULT 0,
            mean_time_ms REAL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (query_fingerprint, table_ref, column_ref, source)
        )
    """,
    "runtime_redaction_log": """
        CREATE TABLE IF NOT EXISTS runtime_redaction_log (
            pattern TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            PRIMARY KEY (pattern, source)
        )
    """,
}

# Index for the common find_unused_columns lookup pattern:
# "show me all calls touching column X over the last 30 days."
RUNTIME_INDEXES: tuple[tuple[str, str], ...] = (
    ("idx_rqc_table_col", "CREATE INDEX IF NOT EXISTS idx_rqc_table_col ON runtime_query_calls (table_ref, column_ref)"),
    ("idx_rqc_last_seen", "CREATE INDEX IF NOT EXISTS idx_rqc_last_seen ON runtime_query_calls (last_seen)"),
)


def ensure_runtime_tables(conn: sqlite3.Connection) -> None:
    """Create runtime tables + indexes if missing. Idempotent."""
    cur = conn.cursor()
    for ddl in RUNTIME_TABLE_SCHEMAS.values():
        cur.execute(ddl)
    for _name, ddl in RUNTIME_INDEXES:
        cur.execute(ddl)
    conn.commit()
