"""data_health_radar — Phase 2 (v1.11.0).

State-snapshot tool: gathers per-column signals from index.json + history
+ runtime tables, then computes the six-axis radar via the pure
``health_radar.compute_radar`` core. Pairs with ``diff_data_health_radar``
for over-time comparisons.

Mirrors jcm's ``get_repo_health`` radar sub-field but produces a
standalone radar payload — jData doesn't have an equivalent of jcm's
broader repo-health composite, so the radar is the headline.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore
from ..runtime.tables import ensure_runtime_tables
from .health_radar import compute_radar


def _drift_status(store: DataStore, dataset: str) -> Optional[bool]:
    """Read history snapshots, return drift status. None when <2 snapshots."""
    snapshots = store.read_history(dataset, n=10)
    if len(snapshots) < 2:
        return None
    first = snapshots[0]
    last = snapshots[-1]
    first_cols = {c["name"] for c in first.get("schema_digest", [])}
    last_cols = {c["name"] for c in last.get("schema_digest", [])}
    if not first_cols:
        return None
    return first_cols == last_cols


def _runtime_coverage_pct(
    db_path,
    column_names: list[str],
    window_days: int,
) -> Optional[float]:
    """Percentage of columns with at least one row in runtime_query_calls in
    the window. Returns None when no traces are ingested at all (axis
    omitted). Returns 0.0 when traces exist but none hit these columns."""
    if not db_path.exists() or not column_names:
        return None
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=window_days)).isoformat()
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_runtime_tables(conn)
        row = conn.execute("SELECT COUNT(*) FROM runtime_query_calls").fetchone()
        if not row or not row[0]:
            return None
        cur = conn.execute(
            """
            SELECT DISTINCT column_ref FROM runtime_query_calls
            WHERE column_ref != '' AND last_seen >= ?
            """,
            (cutoff,),
        )
        hit_cols = {r[0].lower() for r in cur.fetchall() if r[0]}
    finally:
        conn.close()

    wanted = {c.lower() for c in column_names}
    if not wanted:
        return None
    overlap = len(hit_cols & wanted)
    return round(100.0 * overlap / len(wanted), 2)


def data_health_radar(
    dataset: str,
    include_runtime: bool = True,
    window_days: int = 30,
    storage_path: Optional[str] = None,
) -> dict:
    """Six-axis health radar for a single dataset.

    Args:
        dataset: Indexed dataset identifier.
        include_runtime: Fuse the 7th runtime_coverage axis when traces
            exist. Default True. When True but no traces are ingested,
            the axis is omitted (no caveat — the radar is a comparable
            scorecard and silent omission is the contract; check
            ``omitted_axes`` for transparency).
        window_days: Lookback for the runtime axis. Default 30.
        storage_path: Custom data-index root.
    """
    t0 = time.perf_counter()
    store = DataStore(base_path=storage_path or str(get_index_path()))
    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed."}

    cols = idx.columns
    n = len(cols) or 1

    avg_null_pct = sum((c.get("null_pct") or 0.0) for c in cols) / n
    avg_type_conf = sum(c.get("type_confidence", 1.0) for c in cols) / n
    constant_count = sum(1 for c in cols if (c.get("cardinality") or 0) == 1)
    has_pk = any(c.get("is_primary_key_candidate") for c in cols)
    typed = sum(1 for c in cols if c.get("semantic_type"))
    candidates = sum(1 for c in cols if c.get("type") in ("string", "integer", "float"))

    drift_free = _drift_status(store, dataset)

    runtime_pct: Optional[float] = None
    if include_runtime:
        try:
            runtime_pct = _runtime_coverage_pct(
                store.sqlite_path(dataset),
                [c["name"] for c in cols],
                window_days,
            )
        except sqlite3.OperationalError:
            runtime_pct = None

    radar = compute_radar(
        avg_null_pct=avg_null_pct,
        avg_type_confidence=avg_type_conf,
        constant_columns=constant_count,
        total_columns=n,
        has_pk=has_pk,
        typed_columns=typed,
        typeable_candidates=candidates,
        drift_free=drift_free,
        runtime_coverage_pct=runtime_pct,
    )

    return {
        "result": {
            "dataset": dataset,
            "row_count": idx.row_count,
            "column_count": idx.column_count,
            "radar": radar,
        },
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "include_runtime": include_runtime,
            "window_days": window_days,
        },
    }
