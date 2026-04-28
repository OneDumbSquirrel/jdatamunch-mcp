"""Per-dataset learned null tokens (C3)."""

import csv

import pytest

from jdatamunch_mcp.profiler.null_learner import learn_null_tokens
from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.storage.data_store import DataStore


def test_detects_recurring_sentinels():
    profiles = [
        {
            "name": "a",
            "count": 100,
            "value_index": {"actual": 80, "TBD": 10, "999": 10},
        },
        {
            "name": "b",
            "count": 100,
            "value_index": {"real": 90, "TBD": 5, "999": 5},
        },
    ]
    out = learn_null_tokens(profiles)
    tokens = {e["token"] for e in out}
    assert "TBD" in tokens
    assert "999" in tokens


def test_ignores_low_frequency_and_single_column():
    profiles = [
        {"name": "a", "count": 1000, "value_index": {"normal": 999, "TBD": 1}},
        {"name": "b", "count": 1000, "value_index": {"other": 1000}},
    ]
    out = learn_null_tokens(profiles)
    assert all(e["token"] != "TBD" for e in out)


def test_index_local_surfaces_learned_tokens(tmp_path):
    csv_path = tmp_path / "dirty.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["x", "y"])
        rows = []
        for i in range(50):
            rows.append((f"v{i}", "TBD" if i % 4 == 0 else f"u{i}"))
        for r in rows:
            w.writerow(r)
        for i in range(50):
            w.writerow((f"v{i}", "TBD" if i % 5 == 0 else f"u{i}"))

    storage = tmp_path / "data-index"
    storage.mkdir()
    # Add another column where TBD also recurs
    csv2 = tmp_path / "dirty.csv"
    # Actually our single-file test needs TBD in multiple columns. Rewrite:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["x", "y", "z"])
        for i in range(100):
            x = "TBD" if i % 5 == 0 else f"v{i}"
            y = "TBD" if i % 7 == 0 else f"u{i}"
            z = "real"
            w.writerow((x, y, z))

    index_local(path=str(csv_path), name="dirty", storage_path=str(storage))
    idx = DataStore(str(storage)).load("dirty")
    tokens = {e["token"] for e in idx.learned_null_tokens}
    assert "TBD" in tokens
