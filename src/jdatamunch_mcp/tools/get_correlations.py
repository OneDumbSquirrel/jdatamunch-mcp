"""get_correlations tool: Pairwise Pearson correlations between numeric columns."""

import math
import sqlite3
import time
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore
from ..storage import result_cache
from ..storage.sqlite_store import _qcol


# Minimum rows with non-null values in both columns to compute correlation
MIN_PAIRS = 10

# Maximum numeric columns to correlate (avoids O(n^2) blowup on wide tables)
MAX_NUMERIC_COLS = 50


def _pearson_sql(col_a: str, col_b: str) -> str:
    """Build a SQL query that returns the Pearson sums for two columns."""
    qa = _qcol(col_a)
    qb = _qcol(col_b)
    return (
        f"SELECT COUNT(*) AS n, "
        f"SUM({qa}) AS sum_a, SUM({qb}) AS sum_b, "
        f"SUM({qa} * {qa}) AS sum_a2, SUM({qb} * {qb}) AS sum_b2, "
        f"SUM({qa} * {qb}) AS sum_ab "
        f"FROM rows WHERE {qa} IS NOT NULL AND {qb} IS NOT NULL"
    )


def _spearman_sql(col_a: str, col_b: str) -> str:
    """Pearson sums computed over rank-transformed values (B10).

    Uses SQLite window functions to assign per-column ranks across rows
    where both columns are non-null, then runs the same Pearson formula.
    """
    qa = _qcol(col_a)
    qb = _qcol(col_b)
    return (
        f"WITH ranked AS ("
        f"  SELECT "
        f"    CAST(ROW_NUMBER() OVER (ORDER BY {qa}) AS REAL) AS ra, "
        f"    CAST(ROW_NUMBER() OVER (ORDER BY {qb}) AS REAL) AS rb "
        f"  FROM rows WHERE {qa} IS NOT NULL AND {qb} IS NOT NULL"
        f") "
        f"SELECT COUNT(*) AS n, "
        f"SUM(ra) AS sum_a, SUM(rb) AS sum_b, "
        f"SUM(ra * ra) AS sum_a2, SUM(rb * rb) AS sum_b2, "
        f"SUM(ra * rb) AS sum_ab "
        f"FROM ranked"
    )


def _compute_r(row: dict) -> Optional[float]:
    """Compute Pearson r from aggregated sums. Returns None if undefined."""
    n = row["n"]
    if n < MIN_PAIRS:
        return None

    sum_a = row["sum_a"]
    sum_b = row["sum_b"]
    sum_a2 = row["sum_a2"]
    sum_b2 = row["sum_b2"]
    sum_ab = row["sum_ab"]

    if any(v is None for v in (sum_a, sum_b, sum_a2, sum_b2, sum_ab)):
        return None

    numerator = n * sum_ab - sum_a * sum_b
    denom_a = n * sum_a2 - sum_a * sum_a
    denom_b = n * sum_b2 - sum_b * sum_b

    if denom_a <= 0 or denom_b <= 0:
        return None  # zero variance (constant column)

    denominator = math.sqrt(denom_a * denom_b)
    if denominator == 0:
        return None

    r = numerator / denominator
    # Clamp to [-1, 1] to handle floating-point drift
    return max(-1.0, min(1.0, r))


def _strength_label(r: float) -> str:
    """Human-readable label for correlation strength."""
    a = abs(r)
    if a >= 0.9:
        return "very strong"
    if a >= 0.7:
        return "strong"
    if a >= 0.5:
        return "moderate"
    if a >= 0.3:
        return "weak"
    return "negligible"


def get_correlations(
    dataset: str,
    min_abs_correlation: float = 0.3,
    columns: Optional[list] = None,
    top_n: int = 20,
    method: str = "pearson",
    storage_path: Optional[str] = None,
) -> dict:
    """Compute pairwise correlations between numeric columns.

    method='pearson' (default) uses raw values; 'spearman' (B10) uses
    rank-transformed values (robust to outliers + monotonic non-linear
    relationships).

    Returns pairs sorted by |r| descending, filtered to |r| >= min_abs_correlation.
    """
    t0 = time.time()
    method = (method or "pearson").lower()
    if method not in ("pearson", "spearman"):
        return {"error": f"INVALID_METHOD: method must be 'pearson' or 'spearman', got {method!r}"}

    store = DataStore(base_path=storage_path or str(get_index_path()))

    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed. Call index_local first."}

    # Find numeric columns
    numeric_cols = [
        c["name"] for c in idx.columns
        if c["type"] in ("integer", "float")
    ]

    # Filter to requested columns if specified
    if columns:
        col_set = set(columns)
        missing = col_set - {c["name"] for c in idx.columns}
        if missing:
            return {"error": f"INVALID_COLUMN: {sorted(missing)}"}
        numeric_cols = [c for c in numeric_cols if c in col_set]

    if len(numeric_cols) < 2:
        return {
            "result": {
                "dataset": dataset,
                "numeric_columns": len(numeric_cols),
                "correlations": [],
                "message": "Need at least 2 numeric columns to compute correlations.",
            },
            "_meta": {"timing_ms": round((time.time() - t0) * 1000, 1)},
        }

    # Cap to avoid O(n^2) blowup
    if len(numeric_cols) > MAX_NUMERIC_COLS:
        numeric_cols = numeric_cols[:MAX_NUMERIC_COLS]

    # Clamp parameters
    top_n = min(max(1, top_n), 200)
    min_abs_correlation = max(0.0, min(1.0, min_abs_correlation))

    # Cache lookup (B2)
    cache_key = result_cache.make_key("get_correlations", idx.source_hash, {
        "method": method,
        "min_abs_correlation": min_abs_correlation,
        "columns": columns,
        "top_n": top_n,
    })
    cached = result_cache.get(store.dataset_dir(dataset), cache_key, tool="get_correlations")
    if cached is not None:
        cached.setdefault("_meta", {})
        cached["_meta"]["cache_hit"] = True
        cached["_meta"]["timing_ms"] = round((time.time() - t0) * 1000, 1)
        return cached

    # Compute all pairwise correlations via SQLite
    sqlite_path = store.sqlite_path(dataset)
    pairs: list[dict] = []

    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")

        sql_builder = _spearman_sql if method == "spearman" else _pearson_sql

        n_cols = len(numeric_cols)
        for i in range(n_cols):
            for j in range(i + 1, n_cols):
                col_a = numeric_cols[i]
                col_b = numeric_cols[j]
                sql = sql_builder(col_a, col_b)
                row = dict(conn.execute(sql).fetchone())
                r = _compute_r(row)
                if r is not None and abs(r) >= min_abs_correlation:
                    pairs.append({
                        "column_a": col_a,
                        "column_b": col_b,
                        "r": round(r, 4),
                        "abs_r": round(abs(r), 4),
                        "direction": "positive" if r > 0 else "negative",
                        "strength": _strength_label(r),
                        "n_pairs": row["n"],
                    })

    # Sort by absolute correlation descending
    pairs.sort(key=lambda p: p["abs_r"], reverse=True)
    pairs = pairs[:top_n]

    # Remove abs_r from output (was only for sorting)
    for p in pairs:
        del p["abs_r"]

    duration_ms = round((time.time() - t0) * 1000, 1)
    n_pairs_computed = n_cols * (n_cols - 1) // 2

    response = {
        "result": {
            "dataset": dataset,
            "method": method,
            "numeric_columns": len(numeric_cols),
            "pairs_computed": n_pairs_computed,
            "correlations_returned": len(pairs),
            "min_abs_correlation": min_abs_correlation,
            "correlations": pairs,
        },
        "_meta": {"timing_ms": duration_ms, "cache_hit": False},
    }
    result_cache.put(store.dataset_dir(dataset), cache_key, response)
    return response
