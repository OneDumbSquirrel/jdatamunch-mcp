"""find_similar_columns — multi-signal cross-dataset column consolidation (v1.12.0).

Mirrors jcm's ``find_similar_symbols`` and jdoc's ``find_similar_sections``.
Scans every (column_a, column_b) pair across the chosen datasets, fuses
four to five signals into a composite score, clusters via union-find, and
classifies each cluster into a verdict tier.

Signals fused per pair:

| Signal      | Description                                                      |
|-------------|------------------------------------------------------------------|
| name        | Token-overlap Jaccard (snake + camel split + lowercase)          |
| type        | 1.0 same type, 0.5 same numeric family, 0.0 otherwise            |
| value       | Jaccard on top_values when both are low-ish-cardinality          |
| cardinality | 1 - abs(card_ratio_a - card_ratio_b) where ratio = card/rows     |
| embedding   | Cosine on column embeddings (when present on both)               |

Weighting depends on embedding presence:

* with embeddings:    emb 0.50 + name 0.20 + value 0.15 + type 0.10 + card 0.05
* without embeddings: name 0.45 + value 0.30 + type 0.15 + card 0.10

Verdict tiers (composite + signal-shape):

* ``near_duplicate``      composite >= 0.85 AND types match
* ``naming_drift``        composite >= 0.70 AND name_sim < 0.5
* ``parallel_definition`` composite >= 0.70 AND name_sim >= 0.7 (same column across datasets)
* ``overlapping_topic``   composite >= 0.50

Use cases:
* Find duplicate columns to consolidate before a migration.
* Surface naming drift across teams (``email`` vs ``email_address``).
* Detect "same conceptual column" spread across multiple datasets
  (e.g. ``users.email`` and ``customers.email``) that probably wants one
  source of truth.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from ..config import get_index_path
from ..embeddings import cosine_similarity
from ..storage.data_store import DataStore
from ..storage.embedding_store import ColumnEmbeddingStore


# ── Tokenisation ──────────────────────────────────────────────────────────────

_TOKEN_SPLIT_RE = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokens(name: str) -> set[str]:
    """Split a column name into lowercase tokens (snake + camel aware)."""
    if not name:
        return set()
    parts = [p for p in _TOKEN_SPLIT_RE.split(name) if p]
    return {p.lower() for p in parts}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ── Signal scoring ────────────────────────────────────────────────────────────

_NUMERIC_TYPES = {"integer", "float"}


def _type_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a in _NUMERIC_TYPES and b in _NUMERIC_TYPES:
        return 0.5
    return 0.0


def _top_values(col: dict) -> set[str]:
    out: set[str] = set()
    for tv in col.get("top_values") or []:
        v = tv.get("value") if isinstance(tv, dict) else tv
        if v is not None:
            out.add(str(v))
    if not out:
        for v in (col.get("value_index") or {}).keys():
            out.add(str(v))
    return out


def _value_score(a: dict, b: dict) -> float:
    # Skip when either side has very high cardinality — top_values are
    # not meaningful comparators for near-unique columns.
    a_card = a.get("cardinality") or 0
    b_card = b.get("cardinality") or 0
    if a_card > 1000 or b_card > 1000:
        return 0.0
    ta = _top_values(a)
    tb = _top_values(b)
    if not ta or not tb:
        return 0.0
    return _jaccard(ta, tb)


def _card_score(a: dict, b: dict, rows_a: int, rows_b: int) -> float:
    if rows_a <= 0 or rows_b <= 0:
        return 0.0
    ra = min((a.get("cardinality") or 0) / rows_a, 1.0)
    rb = min((b.get("cardinality") or 0) / rows_b, 1.0)
    return max(0.0, 1.0 - abs(ra - rb))


# ── Union-Find ────────────────────────────────────────────────────────────────


class _DSU:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


# ── Main ──────────────────────────────────────────────────────────────────────


def _classify(composite: float, name_sim: float, type_match: bool) -> str:
    if composite >= 0.85 and type_match:
        return "near_duplicate"
    if composite >= 0.70 and name_sim < 0.5:
        return "naming_drift"
    if composite >= 0.70 and name_sim >= 0.7:
        return "parallel_definition"
    return "overlapping_topic"


def find_similar_columns(
    datasets: Optional[list[str]] = None,
    min_score: float = 0.5,
    top_n: int = 50,
    same_type_only: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Cluster similar columns across datasets.

    Args:
        datasets: List of dataset_ids to scan. None = every indexed dataset.
        min_score: Composite-score floor for surfacing a pair. Default 0.5.
        top_n: Hard cap on returned clusters. Default 50, max 200.
        same_type_only: When True, drop pairs whose types don't match
            (cuts noise when caller is looking specifically at
            consolidation candidates). Default False.
        storage_path: Custom data-index root.

    Returns:
        ``{result: {clusters: [...], total_pairs, total_clusters, datasets_scanned},
        _meta}``.
    """
    t0 = time.perf_counter()
    top_n = min(max(1, top_n), 200)
    store = DataStore(base_path=storage_path or str(get_index_path()))

    available = [e["dataset"] for e in store.list_datasets()]
    if datasets is None:
        datasets = available
    else:
        missing = [d for d in datasets if d not in available]
        if missing:
            return {
                "error": f"Unknown dataset(s): {missing}",
                "reason": "dataset_not_found",
                "hint": f"Available datasets: {available}",
            }

    if len(datasets) < 1:
        return {
            "error": "No datasets to scan.",
            "reason": "no_datasets",
            "hint": "Run index_local on at least one CSV/Excel file first.",
        }

    # Gather every column from every dataset, along with parent context.
    entries: list[dict] = []
    embeddings_by_idx: dict[int, list[float]] = {}
    for ds_id in datasets:
        idx = store.load(ds_id)
        if idx is None:
            continue
        try:
            emb_store = ColumnEmbeddingStore(store.sqlite_path(ds_id))
            emb_map = emb_store.get_all()
        except Exception:
            emb_map = {}
        for col in idx.columns:
            i = len(entries)
            entries.append({
                "dataset": ds_id,
                "column": col["name"],
                "type": col.get("type", "unknown"),
                "row_count": idx.row_count or 0,
                "_col": col,
            })
            vec = emb_map.get(col["name"])
            if vec:
                embeddings_by_idx[i] = vec

    n = len(entries)
    if n < 2:
        return {
            "result": {
                "clusters": [],
                "total_pairs": 0,
                "total_clusters": 0,
                "datasets_scanned": datasets,
            },
            "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 1)},
        }

    # Pre-compute token sets — reused N(N-1)/2 times.
    token_sets = [_tokens(e["column"]) for e in entries]

    pairs: list[dict] = []
    dsu = _DSU(n)

    for i in range(n):
        ai = entries[i]
        for j in range(i + 1, n):
            bj = entries[j]
            # Skip same-dataset, same-column pairs (would always match).
            if ai["dataset"] == bj["dataset"] and ai["column"] == bj["column"]:
                continue

            type_s = _type_score(ai["type"], bj["type"])
            if same_type_only and type_s < 1.0:
                continue

            name_s = _jaccard(token_sets[i], token_sets[j])
            value_s = _value_score(ai["_col"], bj["_col"])
            card_s = _card_score(ai["_col"], bj["_col"], ai["row_count"], bj["row_count"])

            vec_a = embeddings_by_idx.get(i)
            vec_b = embeddings_by_idx.get(j)
            emb_s = 0.0
            if vec_a and vec_b and len(vec_a) == len(vec_b):
                emb_s = max(0.0, cosine_similarity(vec_a, vec_b))
                composite = (
                    0.50 * emb_s
                    + 0.20 * name_s
                    + 0.15 * value_s
                    + 0.10 * type_s
                    + 0.05 * card_s
                )
                has_emb = True
            else:
                composite = (
                    0.45 * name_s
                    + 0.30 * value_s
                    + 0.15 * type_s
                    + 0.10 * card_s
                )
                has_emb = False

            if composite < min_score:
                continue

            verdict = _classify(composite, name_s, type_s >= 1.0)
            differs_by = []
            if type_s < 1.0:
                differs_by.append("type")
            if name_s < 0.5:
                differs_by.append("name")
            if value_s < 0.3 and value_s > 0:
                differs_by.append("values")

            pairs.append({
                "a_index": i,
                "b_index": j,
                "a": {"dataset": ai["dataset"], "column": ai["column"], "type": ai["type"]},
                "b": {"dataset": bj["dataset"], "column": bj["column"], "type": bj["type"]},
                "score": round(composite, 4),
                "verdict": verdict,
                "signals": {
                    "name": round(name_s, 3),
                    "type": round(type_s, 3),
                    "value": round(value_s, 3),
                    "cardinality": round(card_s, 3),
                    "embedding": round(emb_s, 3) if has_emb else None,
                },
                "differs_by": differs_by,
            })
            dsu.union(i, j)

    # Cluster: only nodes that participated in at least one surfaced pair.
    member_set = set()
    for p in pairs:
        member_set.add(p["a_index"])
        member_set.add(p["b_index"])

    clusters_by_root: dict[int, dict] = {}
    for idx_ in sorted(member_set):
        root = dsu.find(idx_)
        cluster = clusters_by_root.setdefault(
            root,
            {"members": [], "pairs": [], "verdict": "overlapping_topic", "score": 0.0},
        )
        cluster["members"].append({
            "dataset": entries[idx_]["dataset"],
            "column": entries[idx_]["column"],
            "type": entries[idx_]["type"],
        })

    for p in pairs:
        root = dsu.find(p["a_index"])
        cluster = clusters_by_root[root]
        cluster["pairs"].append({
            "a": p["a"], "b": p["b"], "score": p["score"],
            "verdict": p["verdict"], "signals": p["signals"],
            "differs_by": p["differs_by"],
        })
        if p["score"] > cluster["score"]:
            cluster["score"] = p["score"]
        # Strongest verdict in cluster wins for the cluster-level tier.
        if _verdict_rank(p["verdict"]) > _verdict_rank(cluster["verdict"]):
            cluster["verdict"] = p["verdict"]

    clusters = sorted(
        clusters_by_root.values(),
        key=lambda c: (c["score"], len(c["members"])),
        reverse=True,
    )[:top_n]

    return {
        "result": {
            "clusters": clusters,
            "total_pairs": len(pairs),
            "total_clusters": len(clusters_by_root),
            "datasets_scanned": datasets,
            "columns_scanned": n,
        },
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "min_score": min_score,
            "same_type_only": same_type_only,
            "embeddings_available": len(embeddings_by_idx),
        },
    }


_VERDICT_ORDER = ("overlapping_topic", "naming_drift", "parallel_definition", "near_duplicate")


def _verdict_rank(v: str) -> int:
    try:
        return _VERDICT_ORDER.index(v)
    except ValueError:
        return -1
