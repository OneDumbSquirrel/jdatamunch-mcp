"""Reproducible profiling + deterministic random sampling (A9, A12)."""

import csv
import json
import random

import pytest

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.sample_rows import sample_rows
from jdatamunch_mcp.storage.data_store import DataStore


_SKIP_FIELDS = {"indexed_at", "source_path"}


def _strip_volatile(d):
    if isinstance(d, dict):
        return {k: _strip_volatile(v) for k, v in d.items() if k not in _SKIP_FIELDS}
    if isinstance(d, list):
        return [_strip_volatile(v) for v in d]
    return d


def test_index_local_is_deterministic(tmp_path):
    csv_path = tmp_path / "stable.csv"
    rng = random.Random(0)
    rows = [(i, rng.randint(0, 1000), f"v{i % 17}") for i in range(500)]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "n", "tag"])
        w.writerows(rows)

    storage_a = tmp_path / "a"
    storage_b = tmp_path / "b"
    storage_a.mkdir(); storage_b.mkdir()

    index_local(path=str(csv_path), name="stable", storage_path=str(storage_a))
    index_local(path=str(csv_path), name="stable", storage_path=str(storage_b))

    a = json.loads(DataStore(str(storage_a)).index_path("stable").read_text(encoding="utf-8"))
    b = json.loads(DataStore(str(storage_b)).index_path("stable").read_text(encoding="utf-8"))

    assert _strip_volatile(a) == _strip_volatile(b)


def test_sample_rows_seed_reproducible(tmp_path):
    csv_path = tmp_path / "seed.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["x"])
        for i in range(200):
            w.writerow([i])

    storage = tmp_path / "data-index"
    storage.mkdir()
    index_local(path=str(csv_path), name="seed", storage_path=str(storage))

    a = sample_rows(dataset="seed", n=10, method="random", seed=42, storage_path=str(storage))
    b = sample_rows(dataset="seed", n=10, method="random", seed=42, storage_path=str(storage))
    assert a["result"]["rows"] == b["result"]["rows"]


def test_sample_rows_different_seed_gives_different_output(tmp_path):
    csv_path = tmp_path / "seed2.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["x"])
        for i in range(200):
            w.writerow([i])
    storage = tmp_path / "data-index"
    storage.mkdir()
    index_local(path=str(csv_path), name="seed2", storage_path=str(storage))
    a = sample_rows(dataset="seed2", n=10, method="random", seed=1, storage_path=str(storage))
    b = sample_rows(dataset="seed2", n=10, method="random", seed=2, storage_path=str(storage))
    # Different seeds should pick different rowsets (collision odds vanishingly small).
    assert a["result"]["rows"] != b["result"]["rows"]
