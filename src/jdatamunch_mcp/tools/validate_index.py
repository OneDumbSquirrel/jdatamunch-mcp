"""validate_index tool: integrity checks for an indexed dataset (A5).

Performs:
  1. PRAGMA integrity_check on the dataset SQLite.
  2. Cross-checks SELECT COUNT(*) FROM rows against index.json.row_count.
  3. Verifies SQLite columns match index.json column names.
  4. Verifies index.json content hash against the .sha256 sidecar.
  5. Reports stale-lock state.

Returns a structured report with overall_status: 'ok' | 'warning' | 'error'.
"""

import hashlib
import sqlite3
import time
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore


def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_index(
    dataset: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Validate a dataset's on-disk index. See module docstring."""
    t0 = time.perf_counter()
    store = DataStore(base_path=storage_path or str(get_index_path()))

    findings: list = []
    overall = "ok"

    def fail(msg: str) -> None:
        nonlocal overall
        findings.append({"severity": "error", "message": msg})
        overall = "error"

    def warn(msg: str) -> None:
        nonlocal overall
        findings.append({"severity": "warning", "message": msg})
        if overall == "ok":
            overall = "warning"

    idx_path = store.index_path(dataset)
    sql_path = store.sqlite_path(dataset)
    lock_path = store.lock_path(dataset)
    checksum_path = store.index_checksum_path(dataset)

    # 1. Files present
    if not idx_path.exists():
        return {
            "result": {
                "dataset": dataset,
                "overall_status": "error",
                "findings": [{"severity": "error", "message": "index.json missing — dataset not indexed."}],
            },
            "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 1)},
        }
    if not sql_path.exists():
        fail("data.sqlite missing — dataset cannot be queried.")

    # 2. Stale lock
    if lock_path.exists():
        warn("Stale _lock file present — a prior index_local may have crashed. Re-index to recover.")

    # 3. index.json checksum
    if checksum_path.exists():
        try:
            recorded = checksum_path.read_text(encoding="utf-8").strip()
            actual = _hash_bytes(idx_path.read_bytes())
            if recorded != actual:
                fail(f"index.json checksum mismatch (recorded={recorded[:23]}…, actual={actual[:23]}…).")
        except OSError as e:
            warn(f"Could not verify index.json checksum: {e}")
    else:
        warn("Missing index.json.sha256 sidecar (likely indexed before A4). Re-index to gain crash-safety guarantees.")

    # 4. Load index for further checks
    idx = store.load(dataset)
    if idx is None:
        fail("index.json could not be parsed or migrated.")
        return {
            "result": {
                "dataset": dataset,
                "overall_status": overall,
                "findings": findings,
            },
            "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 1)},
        }

    schema_names = [c["name"] for c in idx.columns]

    # 5. SQLite-level checks
    if sql_path.exists():
        try:
            with sqlite3.connect(str(sql_path)) as conn:
                conn.execute("PRAGMA query_only=1")
                ic = conn.execute("PRAGMA integrity_check").fetchone()
                if ic is None or ic[0] != "ok":
                    fail(f"SQLite integrity_check failed: {ic[0] if ic else 'no result'}.")

                row = conn.execute("SELECT COUNT(*) FROM rows").fetchone()
                actual_rows = int(row[0]) if row else 0
                if actual_rows != idx.row_count:
                    fail(f"row_count mismatch: index.json={idx.row_count}, SQLite={actual_rows}.")

                cols = conn.execute("PRAGMA table_info(rows)").fetchall()
                sqlite_col_names = [c[1] for c in cols]
                if sqlite_col_names != schema_names:
                    fail("Column list in SQLite does not match index.json columns.")
        except sqlite3.DatabaseError as e:
            fail(f"SQLite error: {e}")

    return {
        "result": {
            "dataset": dataset,
            "overall_status": overall,
            "findings": findings,
            "row_count": idx.row_count,
            "column_count": idx.column_count,
        },
        "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 1)},
    }
