"""get_data_hotspots tool: Identify high-risk / high-attention columns in a dataset."""

import math
import time
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore
from ..storage import result_cache
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


# Score weights — calibrated so each signal contributes roughly equally
_W_NULL = 0.4       # null_pct contribution
_W_CARD = 0.3       # cardinality-relative-to-rows contribution
_W_OUTLIER = 0.3    # outlier proxy (numeric range spread relative to mean)

# Thresholds for assessment labels
_HIGH_THRESHOLD = 0.6
_MEDIUM_THRESHOLD = 0.3


def _cardinality_score(col: dict, row_count: int) -> float:
    """0–1 score: extreme cardinality (very low OR near-unique) is suspicious."""
    card = col.get("cardinality") or 0
    if row_count == 0:
        return 0.0
    ratio = card / row_count
    # Very low cardinality on a non-boolean type can indicate encoding issues
    if col["type"] in ("string", "integer") and card <= 1:
        return 0.8
    # Near-unique string columns (could be IDs masquerading as values)
    if col["type"] == "string" and ratio > 0.95:
        return 0.5
    # High cardinality numeric — less suspicious
    return min(ratio, 1.0) * 0.2


def _outlier_score(col: dict) -> float:
    """0–1 outlier proxy for numeric columns using coefficient of variation."""
    if col["type"] not in ("integer", "float"):
        return 0.0
    mn = col.get("min")
    mx = col.get("max")
    mean = col.get("mean")
    if mn is None or mx is None or mean is None or mean == 0:
        return 0.0
    try:
        spread = abs(float(mx) - float(mn))
        cv = spread / abs(float(mean))
        # CV > 5 is very high spread — cap at 1.0
        return min(cv / 5.0, 1.0)
    except (TypeError, ZeroDivisionError, ValueError):
        return 0.0


def get_data_hotspots(
    dataset: str,
    top_n: int = 10,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the highest-risk columns in a dataset.

    Risk combines: null rate, cardinality anomalies, and numeric spread (outlier proxy).
    Analogous to jcodemunch's get_hotspots — use this to decide where to look first.
    top_n capped at 50.
    """
    t0 = time.perf_counter()
    top_n = min(max(1, top_n), 50)
    store = DataStore(base_path=storage_path or str(get_index_path()))

    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed. Call index_local first."}

    cache_key = result_cache.make_key("get_data_hotspots", idx.source_hash, {"top_n": top_n})
    cached = result_cache.get(store.dataset_dir(dataset), cache_key)
    if cached is not None:
        cached.setdefault("_meta", {})
        cached["_meta"]["cache_hit"] = True
        cached["_meta"]["timing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return cached

    row_count = idx.row_count or 1
    scored = []

    for col in idx.columns:
        null_pct = (col.get("null_pct") or 0.0) / 100.0  # 0–1
        card_score = _cardinality_score(col, row_count)
        outlier_score = _outlier_score(col)

        hotspot_score = round(
            _W_NULL * null_pct + _W_CARD * card_score + _W_OUTLIER * outlier_score,
            4,
        )

        if hotspot_score >= _HIGH_THRESHOLD:
            assessment = "high"
        elif hotspot_score >= _MEDIUM_THRESHOLD:
            assessment = "medium"
        else:
            assessment = "low"

        entry: dict = {
            "column": col["name"],
            "type": col["type"],
            "hotspot_score": hotspot_score,
            "assessment": assessment,
            "null_pct": col.get("null_pct") or 0.0,
            "cardinality": col.get("cardinality") or 0,
        }
        if col["type"] in ("integer", "float"):
            entry["min"] = col.get("min")
            entry["max"] = col.get("max")
            entry["mean"] = col.get("mean")
        scored.append(entry)

    scored.sort(key=lambda x: x["hotspot_score"], reverse=True)
    top = scored[:top_n]

    high_count = sum(1 for s in scored if s["assessment"] == "high")
    medium_count = sum(1 for s in scored if s["assessment"] == "medium")

    if high_count > 0:
        overall = "high"
    elif medium_count > 0:
        overall = "medium"
    else:
        overall = "low"

    # Estimate token savings vs returning full describe_dataset
    import json
    response_bytes = len(json.dumps(top).encode("utf-8"))
    tokens_saved = estimate_savings(idx.source_size_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="get_data_hotspots")

    response = {
        "result": {
            "dataset": dataset,
            "total_columns": len(scored),
            "high_risk_columns": high_count,
            "medium_risk_columns": medium_count,
            "overall_assessment": overall,
            "hotspots": top,
        },
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            "cache_hit": False,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    result_cache.put(store.dataset_dir(dataset), cache_key, response)
    return response
