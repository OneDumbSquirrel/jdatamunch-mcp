"""Per-dataset learned null tokens (C3).

The canonical `_NULL_VALUES` set in the profiler covers the universally-
recognized null markers (NA, N/A, null, NaN, …). Real datasets often add
their own conventions: "TBD", "UNKNOWN", "999", "----", "0000-00-00", etc.

This module scans completed column profiles to surface frequent tokens that
*look* like null markers but weren't in the canonical set. The output is
informational — surfaced on the dataset summary so agents can decide whether
to treat them as nulls in downstream filters.

Detection heuristics:
  * Token length ≤ 12 characters.
  * Token appears in ≥ N columns (default 2).
  * Token's frequency-weight (cross-column total) is non-trivial.
  * Token matches a sentinel-like pattern: all-uppercase letters, all
    digits (especially 9-runs), all dashes, "unknown"/"tbd" wording.
"""

from __future__ import annotations

import re
from typing import Iterable


_SENTINEL_RX_PATTERNS = [
    re.compile(r"^9{2,}$"),           # 99, 999, 9999, 99999
    re.compile(r"^0{2,}$"),           # 00, 000, 0000
    re.compile(r"^-{2,}$"),           # ---, ----
    re.compile(r"^\.{2,}$"),          # .., ...
    re.compile(r"^\?+$"),             # ?, ??, ???
    re.compile(r"^x+$", re.IGNORECASE),
    re.compile(r"^_+$"),
]
_SENTINEL_WORDS = frozenset([
    "tbd", "unknown", "missing", "pending", "void", "noinfo", "no_info",
    "none", "blank", "empty", "redacted", "anonymous",
])

_MAX_TOKEN_LEN = 12
_MIN_COLUMNS = 2          # token must appear in this many columns
_MIN_FREQ_PER_COL = 0.005  # at least 0.5% of column's non-null rows
_MAX_RETURNED = 10


def _looks_sentinel(token: str) -> bool:
    if token.lower() in _SENTINEL_WORDS:
        return True
    for rx in _SENTINEL_RX_PATTERNS:
        if rx.match(token):
            return True
    return False


def learn_null_tokens(profiles: Iterable[dict]) -> list:
    """Return a list of {token, columns, total_count} for sentinel-looking
    tokens that recur across columns."""
    # token → {columns: set, total_count: int}
    seen: dict = {}
    for p in profiles:
        if not isinstance(p, dict):
            continue
        col_count = (p.get("count") or 0)
        if col_count <= 0:
            continue
        # Use value_index when available, fall back to top_values
        candidates = []
        if p.get("value_index"):
            for v, c in p["value_index"].items():
                candidates.append((str(v), c))
        elif p.get("top_values"):
            for tv in p["top_values"]:
                candidates.append((str(tv.get("value", "")), tv.get("count", 0)))

        for token, freq in candidates:
            t = token.strip()
            if not t or len(t) > _MAX_TOKEN_LEN:
                continue
            if not _looks_sentinel(t):
                continue
            if freq / col_count < _MIN_FREQ_PER_COL:
                continue
            entry = seen.setdefault(t, {"columns": set(), "total_count": 0})
            entry["columns"].add(p.get("name"))
            entry["total_count"] += freq

    out = []
    for token, entry in seen.items():
        if len(entry["columns"]) >= _MIN_COLUMNS:
            out.append({
                "token": token,
                "columns": sorted(c for c in entry["columns"] if c is not None),
                "total_count": entry["total_count"],
            })
    out.sort(key=lambda x: x["total_count"], reverse=True)
    return out[:_MAX_RETURNED]
