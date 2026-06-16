"""Aggregate result cache (B2).

Tools that perform deterministic aggregations on indexed data — `aggregate`,
`get_correlations`, `get_data_hotspots` — get a cheap key/value cache keyed
on (dataset, source_hash, tool, normalized_args).

Stored as JSON files under `~/.data-index/{dataset}/_cache/{key}.json`.
Invalidated when source_hash changes (re-index changes the hash).
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Optional


_CACHE_DIRNAME = "_cache"

# Per-tool hit/miss counters for analyze_perf (in-memory, process-global).
_stats_lock = threading.Lock()
_hits: dict[str, int] = {}
_misses: dict[str, int] = {}


def _record_access(tool: Optional[str], hit: bool) -> None:
    bucket = tool or "<unknown>"
    with _stats_lock:
        target = _hits if hit else _misses
        target[bucket] = target.get(bucket, 0) + 1


def cache_stats() -> dict:
    """Aggregate cache hit/miss counters (totals + per-tool hit rate)."""
    with _stats_lock:
        hits = dict(_hits)
        misses = dict(_misses)
    by_tool: dict[str, dict] = {}
    total_hits = total_misses = 0
    for name in set(hits) | set(misses):
        h = hits.get(name, 0)
        m = misses.get(name, 0)
        tot = h + m
        by_tool[name] = {
            "hits": h,
            "misses": m,
            "hit_rate": round(h / tot, 3) if tot else 0.0,
        }
        total_hits += h
        total_misses += m
    grand = total_hits + total_misses
    return {
        "total_hits": total_hits,
        "total_misses": total_misses,
        "hit_rate": round(total_hits / grand, 3) if grand else 0.0,
        "by_tool": by_tool,
    }


def reset_stats() -> None:
    """Clear the hit/miss counters (test hook)."""
    with _stats_lock:
        _hits.clear()
        _misses.clear()


def _normalize(obj: Any) -> Any:
    """Convert dict/list into a canonically-ordered JSON-safe structure."""
    if isinstance(obj, dict):
        return {k: _normalize(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def make_key(tool: str, source_hash: str, args: dict) -> str:
    payload = {
        "tool": tool,
        "source_hash": source_hash,
        "args": _normalize(args),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cache_dir(dataset_dir: Path) -> Path:
    d = dataset_dir / _CACHE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def get(dataset_dir: Path, key: str, tool: Optional[str] = None) -> Optional[dict]:
    path = cache_dir(dataset_dir) / f"{key}.json"
    if not path.exists():
        _record_access(tool, hit=False)
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        _record_access(tool, hit=True)
        return value
    except (OSError, json.JSONDecodeError):
        _record_access(tool, hit=False)
        return None


def put(dataset_dir: Path, key: str, value: dict) -> None:
    path = cache_dir(dataset_dir) / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f)
        tmp.replace(path)
    except OSError:
        pass


def invalidate(dataset_dir: Path) -> None:
    """Drop all cached results for a dataset (called on re-index)."""
    d = dataset_dir / _CACHE_DIRNAME
    if not d.exists():
        return
    for f in d.glob("*.json"):
        try:
            f.unlink()
        except OSError:
            pass
