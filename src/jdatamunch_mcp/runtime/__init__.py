"""Runtime traffic ingestion (v1.6.0 — Phase-1 sibling-parity).

Parses pg_stat_statements CSVs and generic SQL log JSONL into the
``runtime_query_calls`` + ``runtime_redaction_log`` tables that
downstream tools (find_unused_columns, check_column_drop_safe,
get_data_hotspots v2, data_health_radar) read from.

Public entry points:

* :func:`parse_sql_log_file` — pure parser, no I/O against the index.
* :func:`ingest_sql_log_file` — full pipeline (parse → redact → resolve → upsert).
* :func:`ensure_runtime_tables` — idempotent CREATE TABLE IF NOT EXISTS.

PII is redacted at the chokepoint by default via
:func:`jdatamunch_mcp.redact.redact_sql_query_text`. Pass ``redact=False``
to ``ingest_sql_log_file`` only on synthetic data — never on prod logs.
"""

from .sql_log import parse_sql_log_file, SqlQueryRecord
from .ingest import ingest_sql_log_file
from .tables import ensure_runtime_tables, RUNTIME_TABLE_SCHEMAS

__all__ = [
    "parse_sql_log_file",
    "ingest_sql_log_file",
    "ensure_runtime_tables",
    "RUNTIME_TABLE_SCHEMAS",
    "SqlQueryRecord",
]
