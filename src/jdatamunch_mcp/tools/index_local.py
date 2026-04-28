"""index_local tool: Index a local CSV/Excel/Parquet/JSONL file into SQLite + index.json.

Crash-safety guarantees (A4):
  * Acquires a `_lock` file in the dataset dir for the duration of indexing.
  * SQLite is written to data.sqlite.tmp; renamed to data.sqlite only after
    profiles are computed successfully. A kill mid-load leaves no half-baked
    rows table visible to readers.
  * On entry, stale `_lock` + missing `index.json` triggers cleanup_stale_artifacts.
  * Final index.json write is atomic + sidecar SHA-256 (data_store.save).
History (A8): on every successful (re)index, appends a snapshot to _history.jsonl.
"""

import time
from pathlib import Path
from typing import Optional

from ..config import get_index_path, get_max_rows
from ..parser import parse_file
from ..profiler.column_profiler import _ColAcc, update_acc, finalize_profile, infer_types_from_sample, _TYPE_FROM_RANK
from ..storage.data_store import DataStore
from ..storage.sqlite_store import create_table, BulkInserter, create_indexes
from ..storage.token_tracker import record_savings, estimate_savings
from ..summarizer import summarize_dataset, summarize_column

_TYPE_SAMPLE_ROWS = 10_000  # rows used for preliminary type detection


def _build_history_snapshot(idx, profiles: list, source_hash: str) -> dict:
    """Compact snapshot for _history.jsonl (A8)."""
    return {
        "indexed_at": idx.indexed_at,
        "source_hash": source_hash,
        "row_count": idx.row_count,
        "column_count": idx.column_count,
        "schema_digest": [
            {
                "name": p.name,
                "type": p.type,
                "null_pct": p.null_pct,
                "cardinality": p.cardinality,
                "semantic_type": getattr(p, "semantic_type", None),
            }
            for p in profiles
        ],
    }


def index_local(
    path: str,
    name: Optional[str] = None,
    incremental: bool = True,
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
    header_row: int = 0,
    sheet: Optional[str] = None,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
) -> dict:
    """Index a local CSV / Excel / Parquet / JSONL file. See module docstring."""
    t0 = time.time()
    store = DataStore(base_path=storage_path or str(get_index_path()))

    p = Path(path)
    dataset_id = name or p.stem.lower().replace(" ", "-")

    # Recover from any prior crash (A4) before deciding whether to skip
    store.cleanup_stale_artifacts(dataset_id)

    # Incremental: skip if file hash unchanged
    if incremental and not store.needs_reindex(dataset_id, str(p)):
        idx = store.load(dataset_id)
        return {
            "result": {
                "dataset": dataset_id,
                "skipped": True,
                "reason": "file unchanged (incremental=true)",
                "rows": idx.row_count if idx else 0,
                "columns": idx.column_count if idx else 0,
                "indexed_at": idx.indexed_at if idx else None,
            },
            "_meta": {
                "timing_ms": round((time.time() - t0) * 1000, 1),
                "tokens_saved": 0,
                "total_tokens_saved": 0,
            },
        }

    try:
        p = p.resolve(strict=True)
    except (FileNotFoundError, OSError) as e:
        return {"error": f"INDEX_ERROR: {e}"}

    try:
        parsed = parse_file(
            path=str(p),
            encoding=encoding,
            delimiter=delimiter,
            header_row=header_row,
            sheet=sheet,
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        return {"error": f"INDEX_ERROR: {e}"}

    columns = parsed.columns
    n_cols = len(columns)
    meta = parsed.metadata
    source_format = p.suffix.lower().lstrip(".")

    # --- Acquire lock + decide on tmp paths (A4) ---
    store.dataset_dir(dataset_id).mkdir(parents=True, exist_ok=True)
    store.acquire_lock(dataset_id)
    final_sqlite = store.sqlite_path(dataset_id)
    tmp_sqlite = final_sqlite.with_suffix(".sqlite.tmp")
    if tmp_sqlite.exists():
        try:
            tmp_sqlite.unlink()
        except OSError:
            pass

    try:
        # --- Phase 1: Read sample rows for type detection ---
        sample_rows: list = []
        row_iter = parsed.row_iterator
        max_rows = get_max_rows()

        for row in row_iter:
            sample_rows.append(row)
            if len(sample_rows) >= _TYPE_SAMPLE_ROWS:
                break

        accs = [_ColAcc(name=col.name, position=col.position) for col in columns]
        infer_types_from_sample(accs, sample_rows)

        preliminary_types = [_TYPE_FROM_RANK[acc.type_rank] for acc in accs]
        column_names = [col.name for col in columns]

        # --- Phase 2: Create SQLite schema (write to .tmp) ---
        create_table(tmp_sqlite, column_names, preliminary_types)

        # --- Phase 3: Full single pass — profile + load SQLite ---
        row_count = 0

        with BulkInserter(tmp_sqlite, column_names, preliminary_types) as inserter:
            for row in sample_rows:
                row_count += 1
                inserter.add(row)
                if row_count >= max_rows:
                    break

            if row_count < max_rows:
                for row in row_iter:
                    row_count += 1
                    for acc, raw in zip(accs, row):
                        update_acc(acc, raw)
                    inserter.add(row)
                    if row_count >= max_rows:
                        break

        # --- Phase 4: Finalize profiles ---
        profiles = [finalize_profile(acc) for acc in accs]

        # --- Phase 5: Create SQLite indexes on low-cardinality columns ---
        create_indexes(tmp_sqlite, profiles)

        # --- Phase 6: Generate summaries ---
        from ..storage.data_store import _profile_to_dict
        col_dicts = [_profile_to_dict(prof) for prof in profiles]
        for prof, col_dict in zip(profiles, col_dicts):
            prof.ai_summary = summarize_column(col_dict)

        ds_summary = summarize_dataset(
            dataset_id=dataset_id,
            columns=col_dicts,
            row_count=row_count,
            source_format=source_format,
            source_size_bytes=meta.get("file_size", 0),
            source_path=str(p.resolve()),
        )

        # --- Atomic SQLite swap (A4): only after profiles are ready ---
        if final_sqlite.exists():
            try:
                final_sqlite.unlink()
            except OSError:
                pass
        # WAL/SHM sidecars are safe to leave for SQLite to recreate
        for ext in (".sqlite-wal", ".sqlite-shm"):
            sidecar = final_sqlite.with_suffix(ext)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
        tmp_sqlite.replace(final_sqlite)

        # --- Phase 7: Save index.json (atomic + sha256 sidecar) ---
        idx = store.save(
            dataset_id=dataset_id,
            profiles=profiles,
            source_path=str(p.resolve()),
            source_format=source_format,
            row_count=row_count,
            encoding=meta.get("encoding", "utf-8"),
            delimiter=meta.get("delimiter") or "",
            dataset_summary=ds_summary,
        )

        # --- Phase 8: Append history snapshot (A8) ---
        try:
            store.append_history(
                dataset_id,
                _build_history_snapshot(idx, profiles, idx.source_hash),
            )
        except Exception:
            pass  # history is best-effort; do not fail the index call

    finally:
        store.release_lock(dataset_id)
        if tmp_sqlite.exists():
            try:
                tmp_sqlite.unlink()
            except OSError:
                pass

    duration_s = time.time() - t0

    index_size = store.index_path(dataset_id).stat().st_size
    tokens_saved = estimate_savings(meta.get("file_size", 0), index_size)
    total_saved = record_savings(tokens_saved, str(store.base_path))

    type_counts: dict = {}
    for p_ in profiles:
        type_counts[p_.type] = type_counts.get(p_.type, 0) + 1

    return {
        "result": {
            "dataset": dataset_id,
            "file": p.name,
            "rows": row_count,
            "columns": n_cols,
            "size_bytes": meta.get("file_size", 0),
            "column_types": type_counts,
            "indexed_at": idx.indexed_at,
            "duration_seconds": round(duration_s, 1),
        },
        "_meta": {
            "timing_ms": round(duration_s * 1000, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
        },
    }
