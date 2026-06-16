"""analyze_perf -- surface per-tool latency and cache-hit telemetry.

Reads the in-memory per-tool latency ring (always populated when call_tool
fires) and, when ``JDATAMUNCH_PERF_TELEMETRY=1``, the persistent perf SQLite
sink. Also surfaces the result-cache hit rate. No-op safe before any calls.

Sibling of jcodemunch-mcp / jdocmunch-mcp ``analyze_perf``.
"""

from __future__ import annotations

import time
from typing import Optional

from .. import perf as _perf
from ..storage import result_cache as _rc

_DEFAULT_TOP = 20

_WINDOW_SECONDS = {
    "1h": 3600.0,
    "24h": 86_400.0,
    "7d": 7 * 86_400.0,
    "all": None,
}


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(pct * len(sorted_vals))))
    return sorted_vals[idx]


def analyze_perf(
    window: str = "session",
    top: int = _DEFAULT_TOP,
    tool: Optional[str] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Per-tool latency + cache-hit telemetry.

    Args:
        window: ``session`` (the in-memory ring, always available) or one of
                ``1h`` / ``24h`` / ``7d`` / ``all`` (reads the persistent perf
                SQLite sink, which requires ``JDATAMUNCH_PERF_TELEMETRY=1``).
        top:    Cap on how many slowest tools / coldest caches to return.
        tool:   Restrict the analysis to a single tool name.
        storage_path: Optional override for the index storage root.

    Returns per-tool p50/p95/max/error_rate, the slowest tools by p95, and
    result-cache hit rates (aggregate / get_correlations / get_data_hotspots
    are the cached tools).
    """
    t0 = time.perf_counter()
    top = max(1, int(top))

    in_memory = _perf.latency_stats()
    if tool:
        in_memory = {k: v for k, v in in_memory.items() if k == tool}

    persisted: dict = {}
    persisted_meta: dict = {"source": "in_memory_only", "rows": 0}
    if window != "session":
        if window not in _WINDOW_SECONDS:
            return {
                "error": (
                    f"Invalid window {window!r}. Use one of: session, 1h, 24h, 7d, all."
                )
            }
        rows = _perf.perf_db_query(
            window_seconds=_WINDOW_SECONDS[window],
            tool=tool,
            storage_path=storage_path,
        )
        persisted_meta = {"source": _perf._PERF_DB, "rows": len(rows), "window": window}
        by_tool: dict[str, list[float]] = {}
        errors: dict[str, int] = {}
        for _ts, t_name, dur, ok in rows:
            by_tool.setdefault(t_name, []).append(float(dur))
            if not ok:
                errors[t_name] = errors.get(t_name, 0) + 1
        for t_name, durs in by_tool.items():
            durs.sort()
            n = len(durs)
            persisted[t_name] = {
                "count": n,
                "p50_ms": round(_percentile(durs, 0.5), 2),
                "p95_ms": round(_percentile(durs, 0.95), 2),
                "max_ms": round(durs[-1], 2),
                "errors": errors.get(t_name, 0),
                "error_rate": round(errors.get(t_name, 0) / n, 3) if n else 0.0,
            }
        if not _perf.telemetry_enabled() and persisted_meta["rows"] == 0:
            persisted_meta["note"] = (
                "No persisted rows. Set JDATAMUNCH_PERF_TELEMETRY=1 to enable "
                "the perf SQLite sink."
            )

    ranked_source = persisted if window != "session" else in_memory
    slowest = sorted(
        ranked_source.items(),
        key=lambda kv: kv[1].get("p95_ms", 0.0),
        reverse=True,
    )[:top]

    cache = _rc.cache_stats()
    coldest = sorted(
        cache.get("by_tool", {}).items(),
        key=lambda kv: kv[1].get("hit_rate", 0.0),
    )[:top]

    return {
        "window": window,
        "tool": tool,
        "in_memory_session": in_memory,
        "persisted": persisted,
        "persisted_meta": persisted_meta,
        "slowest_by_p95": [{"tool": name, **stats} for name, stats in slowest],
        "cache": {
            "totals": {
                "hits": cache.get("total_hits", 0),
                "misses": cache.get("total_misses", 0),
                "hit_rate": cache.get("hit_rate", 0.0),
            },
            "coldest_by_tool": [{"tool": name, **stats} for name, stats in coldest],
        },
        "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 2)},
    }
