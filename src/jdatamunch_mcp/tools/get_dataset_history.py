"""get_dataset_history tool: Read profile snapshots from _history.jsonl (A8).

Snapshots are appended on every successful index_local. Use this to detect
schema/content drift over multiple ingests of the same dataset without
needing two separately-named indexes.
"""

import time
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore


def get_dataset_history(
    dataset: str,
    n: int = 10,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the last n profile snapshots for a dataset (chronological order)."""
    t0 = time.perf_counter()
    n = min(max(1, n), 50)
    store = DataStore(base_path=storage_path or str(get_index_path()))

    if store.load(dataset) is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed."}

    snapshots = store.read_history(dataset, n=n)

    return {
        "result": {
            "dataset": dataset,
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
        },
        "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 1)},
    }
