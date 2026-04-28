"""Profile snapshots / dataset history (A8)."""

import csv

import pytest

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.get_dataset_history import get_dataset_history


def _write_csv(path, rows, header=("a", "b")):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def test_history_appended_per_index(tmp_path):
    csv_path = tmp_path / "drift.csv"
    storage = tmp_path / "data-index"
    storage.mkdir()

    _write_csv(csv_path, [(i, i * 2) for i in range(10)])
    index_local(path=str(csv_path), name="drift", storage_path=str(storage))

    # Re-write with different content + force re-index by changing the file
    _write_csv(csv_path, [(i, i * 3) for i in range(15)])
    index_local(path=str(csv_path), name="drift", storage_path=str(storage))

    res = get_dataset_history(dataset="drift", storage_path=str(storage))
    assert "error" not in res
    snaps = res["result"]["snapshots"]
    assert len(snaps) == 2
    # Snapshots are chronological; second has more rows.
    assert snaps[0]["row_count"] == 10
    assert snaps[1]["row_count"] == 15
    # Schema digest captures column names per snapshot.
    assert {c["name"] for c in snaps[0]["schema_digest"]} == {"a", "b"}


def test_history_n_param_caps_returned_count(tmp_path):
    csv_path = tmp_path / "x.csv"
    storage = tmp_path / "data-index"
    storage.mkdir()
    _write_csv(csv_path, [(1, 2)])
    index_local(path=str(csv_path), name="x", storage_path=str(storage))
    res = get_dataset_history(dataset="x", n=5, storage_path=str(storage))
    assert res["result"]["snapshot_count"] == 1


def test_history_missing_dataset(tmp_path):
    storage = tmp_path / "data-index"
    storage.mkdir()
    res = get_dataset_history(dataset="never", storage_path=str(storage))
    assert "error" in res
