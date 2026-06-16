"""Per-tool latency telemetry for analyze_perf.

An in-memory per-tool ring (always populated when call_tool fires) plus an
optional persistent SQLite sink gated by ``JDATAMUNCH_PERF_TELEMETRY=1``. This
is the data source behind the ``analyze_perf`` tool and mirrors the shape
jcodemunch-mcp / jdocmunch-mcp expose, in jData's idiom.

Recording is best-effort and must never break a tool call: the in-memory path
is a lock-guarded deque append; the persistent path swallows every error.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from .config import get_index_path

_RING_MAX = 500  # retained samples per tool (in-memory)
_PERF_DB = "perf_telemetry.db"
_DEFAULT_MAX_ROWS = 100_000

_lock = threading.Lock()
_rings: dict[str, deque] = defaultdict(lambda: deque(maxlen=_RING_MAX))
_errors: dict[str, int] = defaultdict(int)


def telemetry_enabled() -> bool:
    return os.environ.get("JDATAMUNCH_PERF_TELEMETRY", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _max_rows() -> int:
    try:
        return int(os.environ.get("JDATAMUNCH_PERF_TELEMETRY_MAX_ROWS", _DEFAULT_MAX_ROWS))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ROWS


def record(
    tool: str,
    duration_ms: float,
    ok: bool = True,
    storage_path: Optional[str] = None,
) -> None:
    """Record one tool call's latency. In-memory always; SQLite if enabled."""
    with _lock:
        _rings[tool].append(float(duration_ms))
        if not ok:
            _errors[tool] += 1
    if telemetry_enabled():
        try:
            _persist(tool, duration_ms, ok, storage_path)
        except Exception:
            # Telemetry must never break a tool call.
            pass


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(pct * len(sorted_vals))))
    return sorted_vals[idx]


def _summarize(durations: list[float], errors: int) -> dict:
    durs = sorted(durations)
    n = len(durs)
    return {
        "count": n,
        "p50_ms": round(_percentile(durs, 0.5), 2),
        "p95_ms": round(_percentile(durs, 0.95), 2),
        "max_ms": round(durs[-1], 2) if durs else 0.0,
        "errors": errors,
        "error_rate": round(errors / n, 3) if n else 0.0,
    }


def latency_stats() -> dict:
    """Per-tool p50/p95/max/error_rate over the in-memory session ring."""
    with _lock:
        snapshot = {t: list(d) for t, d in _rings.items()}
        errs = dict(_errors)
    return {
        tool: _summarize(vals, errs.get(tool, 0))
        for tool, vals in snapshot.items()
        if vals
    }


def reset() -> None:
    """Clear the in-memory ring (test hook)."""
    with _lock:
        _rings.clear()
        _errors.clear()


# --------------------------------------------------------------------------- #
# Persistent SQLite sink (opt-in)                                             #
# --------------------------------------------------------------------------- #
def _db_path(storage_path: Optional[str] = None) -> Path:
    root = get_index_path(storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root / _PERF_DB


def _connect(storage_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(storage_path)))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS perf_calls "
        "(ts REAL, tool TEXT, duration_ms REAL, ok INTEGER)"
    )
    return conn


def _persist(tool: str, duration_ms: float, ok: bool, storage_path: Optional[str]) -> None:
    conn = _connect(storage_path)
    try:
        conn.execute(
            "INSERT INTO perf_calls (ts, tool, duration_ms, ok) VALUES (?, ?, ?, ?)",
            (time.time(), tool, float(duration_ms), 1 if ok else 0),
        )
        conn.commit()
        cap = _max_rows()
        count = conn.execute("SELECT COUNT(*) FROM perf_calls").fetchone()[0]
        if count > cap:
            conn.execute(
                "DELETE FROM perf_calls WHERE rowid IN "
                "(SELECT rowid FROM perf_calls ORDER BY ts ASC LIMIT ?)",
                (count - cap,),
            )
            conn.commit()
    finally:
        conn.close()


def perf_db_query(
    window_seconds: Optional[float] = None,
    tool: Optional[str] = None,
    storage_path: Optional[str] = None,
) -> list[tuple]:
    """Return (ts, tool, duration_ms, ok) rows from the persistent sink."""
    if not _db_path(storage_path).exists():
        return []
    conn = _connect(storage_path)
    try:
        query = "SELECT ts, tool, duration_ms, ok FROM perf_calls"
        clauses: list[str] = []
        params: list = []
        if window_seconds is not None:
            clauses.append("ts >= ?")
            params.append(time.time() - window_seconds)
        if tool:
            clauses.append("tool = ?")
            params.append(tool)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        return list(conn.execute(query, params).fetchall())
    finally:
        conn.close()
