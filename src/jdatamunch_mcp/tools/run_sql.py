"""run_sql tool: read-only sandboxed SQL escape hatch (B1).

Accepts a single SELECT (or WITH ... SELECT) statement that references one
or more indexed datasets by name. Each named dataset is ATTACHed under its
own schema name. Statement is executed under PRAGMA query_only=1 with a
hard row cap and a statement timeout.

This is the supported way to express HAVING / window functions / multi-way
JOINs / CTEs without building a tool surface for every variant.

Safety:
  * Pure-Python validation rejects anything that isn't a single SELECT/WITH.
  * Multiple statements (semicolons) rejected.
  * Forbidden keywords (INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA/...) rejected
    even inside SELECT to defend against creative injection.
  * Hard limits applied via post-fetch row truncation.
"""

import re
import sqlite3
import time
from typing import Optional

from ..config import get_index_path
from ..redact import redact_rows, redaction_meta
from ..storage.data_store import DataStore


_MAX_ROWS_RETURNED = 500
_MAX_DATASETS = 10
_STATEMENT_TIMEOUT_S = 10.0

_FORBIDDEN_RX = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|DROP|CREATE|ALTER|TRUNCATE|"
    r"ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|ANALYZE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_LEADING_RX = re.compile(r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)+", re.DOTALL)


def _strip_leading_noise(sql: str) -> str:
    """Strip leading whitespace + comments to find the first keyword."""
    while True:
        m = _LEADING_RX.match(sql)
        if not m or not m.group(0):
            break
        sql = sql[m.end():]
    return sql


def _validate(sql: str) -> Optional[str]:
    """Return error message if SQL is unsafe; None if it passes the gate."""
    if not sql or not sql.strip():
        return "INVALID_SQL: empty statement"
    # No multi-statement
    no_trailing = sql.rstrip().rstrip(";").strip()
    if ";" in no_trailing:
        return "INVALID_SQL: only a single statement is allowed"
    leading = _strip_leading_noise(no_trailing).lstrip()
    head = leading[:6].upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        return "INVALID_SQL: only SELECT or WITH ... SELECT statements allowed"
    if _FORBIDDEN_RX.search(no_trailing):
        return "INVALID_SQL: contains a forbidden keyword (INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA/...)"
    return None


def run_sql(
    sql: str,
    datasets: list,
    limit: int = _MAX_ROWS_RETURNED,
    redact: bool = True,
    redact_patterns: Optional[list] = None,
    redact_skip_columns: Optional[list] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Execute a read-only SELECT against one or more indexed datasets (B1).

    `datasets[0]` is attached as the main connection (its `rows` table is
    accessible without schema prefix). Additional datasets are attached
    under the schema name `<dataset>` and accessed as `<dataset>.rows`.
    """
    t0 = time.perf_counter()
    if not datasets:
        return {"error": "INVALID_SQL: at least one dataset must be provided"}
    if len(datasets) > _MAX_DATASETS:
        return {"error": f"TOO_MANY_DATASETS: max {_MAX_DATASETS}"}

    err = _validate(sql)
    if err:
        return {"error": err}

    limit = min(max(1, limit), _MAX_ROWS_RETURNED)

    store = DataStore(base_path=storage_path or str(get_index_path()))
    sqlite_paths: list = []
    for name in datasets:
        idx = store.load(name)
        if idx is None:
            return {"error": f"NOT_INDEXED: dataset {name!r} is not indexed."}
        sp = store.sqlite_path(name)
        if not sp.exists():
            return {"error": f"NOT_INDEXED: SQLite missing for {name!r}."}
        sqlite_paths.append((name, sp))

    main_name, main_path = sqlite_paths[0]
    try:
        with sqlite3.connect(str(main_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=1")
            # Statement timeout via interrupt() on a wall-clock budget
            deadline = time.monotonic() + _STATEMENT_TIMEOUT_S

            def _progress():
                if time.monotonic() > deadline:
                    return 1
                return 0

            conn.set_progress_handler(_progress, 1000)

            # ATTACH siblings as additional schemas (read-only via query_only)
            for name, sp in sqlite_paths[1:]:
                # Names safe — keys come from store.load and are filename-derived
                conn.execute(f"ATTACH DATABASE ? AS {_safe_schema(name)}", [str(sp)])

            cursor = conn.execute(sql)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows: list = []
            for row in cursor:
                rows.append({c: row[c] for c in cols})
                if len(rows) >= limit:
                    break

            redaction_summary: Optional[dict] = None
            if redact:
                rows, redaction_summary = redact_rows(
                    rows,
                    custom_patterns=redact_patterns,
                    skip_columns=redact_skip_columns,
                )

            return {
                "result": {
                    "datasets": datasets,
                    "columns": cols,
                    "rows": rows,
                    "returned": len(rows),
                    "row_cap": limit,
                },
                "_meta": {
                    "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "redaction": redaction_meta(
                        applied=redact,
                        summary=redaction_summary,
                        custom_patterns=redact_patterns,
                    ),
                },
            }
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "interrupt" in msg.lower():
            return {"error": "QUERY_TIMEOUT: statement exceeded the budget"}
        return {"error": f"SQL_ERROR: {msg}"}
    except sqlite3.DatabaseError as e:
        return {"error": f"SQL_ERROR: {e}"}


_SCHEMA_NAME_RX = re.compile(r"[^A-Za-z0-9_]")


def _safe_schema(name: str) -> str:
    """Derive a SQLite-safe schema alias from a dataset id."""
    s = _SCHEMA_NAME_RX.sub("_", name)
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "ds_" + s
    return s
