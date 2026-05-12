"""get_schema_impact — transitive impact of a schema change (v1.9.0).

Fourth and final Phase-1 sibling-parity tool. Answers: *if I rename /
drop / retype this column, what breaks downstream?*

Inspired by jcodemunch-mcp's ``get_blast_radius``, ported to jData's
FK-graph + runtime-traffic shape. Walks the inferred foreign-key graph
to ``max_depth`` (default 3) and classifies each hit by:

* ``fk_source``    — affected dataset is FK-source-pointed-at-us
* ``fk_target``    — we point at it as the FK target
* ``cross_dataset_name_match`` — same column-name elsewhere; loose
* ``runtime_traffic`` — runtime_query_calls hit this column in window

Three change kinds:

* ``drop_column``    — surface every dataset / runtime query that
  *might* reference this column (FK edges + runtime hits +
  cross-dataset name match).
* ``rename_column``  — same surfaces, plus the recommended action notes
  the new name so callers can plan the cascade.
* ``retype_column``  — additionally checks ``new_type`` against each
  FK-related column's type and flags ``type_mismatch`` entries when
  the join wouldn't work after the retype.

Returns ``direct_impact`` (depth 1), ``transitive_impact`` (depth ≥ 2),
``summary`` of counts, and a normalised ``blast_score`` ∈ [0, 1] so
blast radius is comparable across datasets of different size.

Read-only.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from ..storage.data_store import DataStore
from ..runtime.tables import ensure_runtime_tables


_MAX_IMPACT_ITEMS = 50
_RUNTIME_WINDOW_DAYS = 30

_VALID_KINDS = ("drop_column", "rename_column", "retype_column")


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _find_column(idx, name: str) -> Optional[dict]:
    lower = name.lower()
    for col in idx.columns:
        if (col.get("name") or "").lower() == lower:
            return col
    return None


def _fk_source_hits(store: DataStore, self_dataset: str, col_name: str) -> list[dict]:
    """If ``self_dataset.col_name`` looks like an FK into another dataset's
    PK, return the (potentially multiple) candidate targets.

    Same heuristic as check_column_drop_safe but returns all candidates,
    not just the strongest.
    """
    target = col_name.lower()
    stem = target[:-3] if target.endswith("_id") else None
    out: list[dict] = []
    for entry in store.list_datasets():
        ds_id = entry["dataset"]
        if ds_id == self_dataset:
            continue
        idx = store.load(ds_id)
        if idx is None:
            continue
        for pk in (c for c in idx.columns if c.get("is_primary_key_candidate")):
            pk_name = (pk.get("name") or "").lower()
            ds_lower = ds_id.lower()
            if pk_name == target:
                out.append({
                    "target_dataset": ds_id,
                    "target_column": pk["name"],
                    "target_type": pk.get("type"),
                    "match_kind": "name_match",
                })
                continue
            if stem and pk_name == "id" and ds_lower in (stem, stem + "s"):
                out.append({
                    "target_dataset": ds_id,
                    "target_column": pk["name"],
                    "target_type": pk.get("type"),
                    "match_kind": "stem_match",
                })
    return out


def _fk_target_hits(store: DataStore, self_dataset: str, pk_col_name: str) -> list[dict]:
    """Datasets carrying a column shaped like an FK into ``self_dataset.pk_col_name``."""
    target = pk_col_name.lower()
    self_lower = self_dataset.lower()
    fk_names = {target}
    if target == "id":
        sing = self_lower[:-1] if self_lower.endswith("s") else self_lower
        fk_names.add(f"{sing}_id")
        fk_names.add(f"{self_lower}_id")
    out: list[dict] = []
    for entry in store.list_datasets():
        ds_id = entry["dataset"]
        if ds_id == self_dataset:
            continue
        idx = store.load(ds_id)
        if idx is None:
            continue
        for col in idx.columns:
            cn = (col.get("name") or "").lower()
            if cn in fk_names and not col.get("is_primary_key_candidate"):
                out.append({
                    "source_dataset": ds_id,
                    "source_column": col["name"],
                    "source_type": col.get("type"),
                })
                break
    return out


def _cross_dataset_name_hits(
    store: DataStore, self_dataset: str, col_name: str
) -> list[dict]:
    target = col_name.lower()
    out: list[dict] = []
    for entry in store.list_datasets():
        ds_id = entry["dataset"]
        if ds_id == self_dataset:
            continue
        idx = store.load(ds_id)
        if idx is None:
            continue
        for col in idx.columns:
            if (col.get("name") or "").lower() == target:
                out.append({
                    "dataset": ds_id,
                    "column": col["name"],
                    "type": col.get("type"),
                })
                break
        if len(out) >= _MAX_IMPACT_ITEMS:
            break
    return out


def _runtime_traffic(
    store: DataStore,
    dataset_id: str,
    col_name: str,
    window_days: int,
) -> dict:
    """Return runtime hits for ``dataset_id.col_name`` over the window."""
    db_path = store.sqlite_path(dataset_id)
    if not db_path.exists():
        return {"calls": 0, "last_seen": None, "data_present": False}
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=window_days)).isoformat()
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_runtime_tables(conn)
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN last_seen >= ? THEN calls ELSE 0 END), 0),
                    MAX(last_seen)
                FROM runtime_query_calls
                WHERE source = 'sql_log'
                  AND column_ref = ?
                """,
                (cutoff, col_name.lower()),
            ).fetchone()
            has_any = conn.execute(
                "SELECT 1 FROM runtime_query_calls LIMIT 1"
            ).fetchone() is not None
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return {"calls": 0, "last_seen": None, "data_present": False}
    return {
        "calls": int(row[0] or 0) if row else 0,
        "last_seen": row[1] if row else None,
        "data_present": has_any,
    }


def _types_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """Loose type compatibility check for FK joins.

    Same canonical type → compatible. ``integer``/``string`` cross-type
    is incompatible; everything else falls back to string-equality on
    the type tag.
    """
    if a is None or b is None:
        return True
    a = a.lower()
    b = b.lower()
    if a == b:
        return True
    # Numeric-ish families
    numerics = {"integer", "float", "number"}
    if a in numerics and b in numerics:
        return True
    return False


def _blast_score(
    direct_count: int,
    transitive_count: int,
    runtime_hits: int,
    fk_edges_broken: int,
    total_datasets: int,
) -> float:
    """Normalised 0..1 blast score.

    FK edges weigh more than name-matches; runtime hits count when
    we have data. Soft-normalised against the index size so a 5-edge
    impact in a 50-dataset warehouse scores higher than the same 5
    in a 500-dataset one.
    """
    if total_datasets <= 0:
        return 0.0
    weighted = (
        2.0 * fk_edges_broken
        + 1.0 * direct_count
        + 0.5 * transitive_count
        + (0.5 if runtime_hits > 0 else 0.0)
    )
    denom = max(1.0, 0.3 * total_datasets + 1)
    return round(min(1.0, weighted / denom), 3)


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def get_schema_impact(
    dataset_id: str,
    column: str,
    kind: Literal["drop_column", "rename_column", "retype_column"] = "drop_column",
    new_name: Optional[str] = None,
    new_type: Optional[str] = None,
    max_depth: int = 3,
    window_days: int = _RUNTIME_WINDOW_DAYS,
    storage_path: Optional[str] = None,
) -> dict:
    """Transitive impact of a column-level schema change.

    Args:
        dataset_id: The indexed dataset (= one logical table in jData).
        column: Column name (case-insensitive).
        kind: ``drop_column`` (default), ``rename_column``, or
            ``retype_column``.
        new_name: Required for ``rename_column``. Surfaces in the
            recommended action.
        new_type: Required for ``retype_column``. Drives the
            type-mismatch detector at FK edges.
        max_depth: BFS depth over the inferred FK graph. Default 3.
        window_days: Runtime traffic look-back. Default 30.
        storage_path: Custom data-index root.

    Returns:
        ``{result: {target, direct_impact, transitive_impact, summary,
        blast_score, ...}, _meta}`` on success.
    """
    t0 = time.perf_counter()
    if kind not in _VALID_KINDS:
        return {
            "error": f"Unsupported change kind: {kind!r}",
            "reason": "invalid_kind",
            "hint": f"Expected one of {_VALID_KINDS}.",
        }
    if kind == "rename_column" and not new_name:
        return {
            "error": "rename_column requires new_name.",
            "reason": "missing_new_name",
        }
    if kind == "retype_column" and not new_type:
        return {
            "error": "retype_column requires new_type.",
            "reason": "missing_new_type",
        }

    store = DataStore(base_path=storage_path)
    idx = store.load(dataset_id)
    if idx is None:
        return {
            "error": f"Dataset not indexed: {dataset_id}",
            "reason": "dataset_not_found",
            "hint": "Run index_local first.",
        }

    col = _find_column(idx, column)
    if col is None:
        return {
            "error": f"Column not found in dataset {dataset_id!r}: {column}",
            "reason": "column_not_found",
        }
    canonical = col.get("name") or column
    self_type = col.get("type")

    # --- Depth 1 ---------------------------------------------------------
    direct: list[dict] = []
    fk_edges_broken = 0
    type_mismatches: list[dict] = []

    is_pk = bool(col.get("is_primary_key_candidate"))
    fk_sources = _fk_source_hits(store, dataset_id, canonical) if not is_pk else []
    fk_targets = _fk_target_hits(store, dataset_id, canonical) if is_pk else []
    cross_hits = _cross_dataset_name_hits(store, dataset_id, canonical)
    runtime = _runtime_traffic(store, dataset_id, canonical, window_days)

    for fs in fk_sources:
        fk_edges_broken += 1
        entry = {
            "depth": 1,
            "kind": "fk_source",
            "dataset": dataset_id,
            "column": canonical,
            "target_dataset": fs["target_dataset"],
            "target_column": fs["target_column"],
            "via": fs["match_kind"],
        }
        if kind == "retype_column" and not _types_compatible(new_type, fs.get("target_type")):
            type_mismatches.append({
                "edge": f"{dataset_id}.{canonical} → {fs['target_dataset']}.{fs['target_column']}",
                "old_type": self_type, "new_type": new_type, "partner_type": fs.get("target_type"),
            })
            entry["type_mismatch"] = True
        direct.append(entry)

    for ft in fk_targets:
        fk_edges_broken += 1
        entry = {
            "depth": 1,
            "kind": "fk_target",
            "dataset": ft["source_dataset"],
            "column": ft["source_column"],
            "via_dataset": dataset_id,
            "via_column": canonical,
        }
        if kind == "retype_column" and not _types_compatible(new_type, ft.get("source_type")):
            type_mismatches.append({
                "edge": f"{ft['source_dataset']}.{ft['source_column']} → {dataset_id}.{canonical}",
                "old_type": self_type, "new_type": new_type, "partner_type": ft.get("source_type"),
            })
            entry["type_mismatch"] = True
        direct.append(entry)

    seen_docs = {(dataset_id, canonical)}
    seen_docs.update((d["dataset"], d["column"]) for d in direct if "column" in d)

    for ch in cross_hits:
        # Skip a cross-dataset hit that we already surfaced as an FK edge.
        if (ch["dataset"], ch["column"]) in seen_docs:
            continue
        direct.append({
            "depth": 1,
            "kind": "cross_dataset_name_match",
            "dataset": ch["dataset"],
            "column": ch["column"],
            "type": ch.get("type"),
        })
        seen_docs.add((ch["dataset"], ch["column"]))

    if runtime["data_present"] and runtime["calls"] > 0:
        direct.append({
            "depth": 1,
            "kind": "runtime_traffic",
            "dataset": dataset_id,
            "column": canonical,
            "calls_in_window": runtime["calls"],
            "last_seen": runtime["last_seen"],
        })

    # --- Depth 2..N: walk FK edges from each depth-(d-1) hit -----------
    # Treat each fk_source target / fk_target source as a new pivot and
    # walk one more hop. Cross-dataset and runtime entries don't open
    # new edges.
    transitive: list[dict] = []
    seen_datasets = {dataset_id}
    seen_datasets.update(d["dataset"] for d in direct)
    frontier = [(d["dataset"], d.get("column")) for d in direct
                if d["kind"] in ("fk_source", "fk_target")]

    for depth in range(2, max_depth + 1):
        next_frontier: list[tuple[str, Optional[str]]] = []
        for ds, col_name in frontier:
            if not col_name:
                continue
            idx2 = store.load(ds)
            if idx2 is None:
                continue
            col2 = _find_column(idx2, col_name)
            if col2 is None:
                continue
            is_pk2 = bool(col2.get("is_primary_key_candidate"))
            fk_s2 = _fk_source_hits(store, ds, col_name) if not is_pk2 else []
            fk_t2 = _fk_target_hits(store, ds, col_name) if is_pk2 else []
            for fs in fk_s2:
                key = (fs["target_dataset"], fs["target_column"])
                if fs["target_dataset"] in seen_datasets:
                    continue
                seen_datasets.add(fs["target_dataset"])
                transitive.append({
                    "depth": depth,
                    "kind": "fk_source",
                    "dataset": ds,
                    "column": col_name,
                    "target_dataset": fs["target_dataset"],
                    "target_column": fs["target_column"],
                    "via": fs["match_kind"],
                })
                next_frontier.append(key)
                if len(transitive) >= _MAX_IMPACT_ITEMS:
                    break
            for ft in fk_t2:
                if ft["source_dataset"] in seen_datasets:
                    continue
                seen_datasets.add(ft["source_dataset"])
                transitive.append({
                    "depth": depth,
                    "kind": "fk_target",
                    "dataset": ft["source_dataset"],
                    "column": ft["source_column"],
                    "via_dataset": ds,
                    "via_column": col_name,
                })
                next_frontier.append((ft["source_dataset"], ft["source_column"]))
                if len(transitive) >= _MAX_IMPACT_ITEMS:
                    break
        if not next_frontier:
            break
        frontier = next_frontier

    # --- Summary ---------------------------------------------------------
    all_items = direct + transitive
    datasets_affected = {d["dataset"] for d in all_items} - {dataset_id}
    total_datasets = max(1, len(store.list_datasets()))

    summary = {
        "datasets_affected": len(datasets_affected),
        "direct_count": len(direct),
        "transitive_count": len(transitive),
        "fk_edges_broken": fk_edges_broken,
        "runtime_calls_in_window": runtime["calls"] if runtime["data_present"] else None,
        "type_mismatches": type_mismatches if kind == "retype_column" else [],
        "cross_dataset_name_matches": [
            d["dataset"] for d in direct if d["kind"] == "cross_dataset_name_match"
        ],
    }

    score = _blast_score(
        direct_count=len(direct),
        transitive_count=len(transitive),
        runtime_hits=runtime["calls"] if runtime["data_present"] else 0,
        fk_edges_broken=fk_edges_broken,
        total_datasets=total_datasets,
    )

    # Recommended action mirrors the change kind.
    if kind == "drop_column":
        action_verb = "drop"
    elif kind == "rename_column":
        action_verb = f"rename to {new_name!r}"
    else:
        action_verb = f"retype to {new_type!r}"

    if summary["fk_edges_broken"] > 0:
        recommended = (
            f"Before {action_verb}: update {summary['fk_edges_broken']} FK "
            f"edge(s) across {len(datasets_affected)} other dataset(s)."
        )
    elif summary["runtime_calls_in_window"]:
        recommended = (
            f"Before {action_verb}: investigate {summary['runtime_calls_in_window']} "
            f"recent query call(s) in the last {window_days} day(s)."
        )
    elif summary["cross_dataset_name_matches"]:
        recommended = (
            f"Before {action_verb}: confirm cross-dataset coupling on "
            f"{len(summary['cross_dataset_name_matches'])} same-named column(s)."
        )
    elif kind == "retype_column" and not _types_compatible(new_type, self_type):
        recommended = (
            f"Retype from {self_type} → {new_type} is non-trivial. Validate "
            "no existing values violate the new type before applying."
        )
    else:
        if not runtime["data_present"]:
            recommended = (
                f"No structural blockers detected. Safe to {action_verb} based "
                "on static signals — but ingest_sql_log first to verify no "
                "live traffic depends on this column."
            )
        else:
            recommended = f"No blockers detected. Safe to {action_verb}."

    return {
        "result": {
            "dataset": dataset_id,
            "column": canonical,
            "change": {
                "kind": kind,
                "new_name": new_name,
                "new_type": new_type,
            },
            "direct_impact": direct,
            "transitive_impact": transitive,
            "summary": summary,
            "blast_score": score,
            "recommended_action": recommended,
        },
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "max_depth": max_depth,
            "window_days": window_days,
            "runtime_data_present": runtime["data_present"],
            "total_datasets": total_datasets,
        },
    }
