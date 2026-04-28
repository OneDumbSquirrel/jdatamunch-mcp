"""Dataset content fingerprint (C2)."""

import csv
import shutil

import pytest

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.list_datasets import list_datasets
from jdatamunch_mcp.storage.data_store import DataStore


def _write(p, rows, header=("a", "b")):
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def test_fingerprint_present(tmp_path):
    p = tmp_path / "x.csv"
    _write(p, [(1, 2), (3, 4)])
    storage = tmp_path / "data-index"
    storage.mkdir()
    index_local(path=str(p), name="x", storage_path=str(storage))

    idx = DataStore(str(storage)).load("x")
    assert idx.fingerprint
    assert idx.fingerprint.startswith("sha256:")

    listing = list_datasets(storage_path=str(storage))
    fps = {r["dataset"]: r.get("fingerprint") for r in listing["result"]}
    assert fps["x"] == idx.fingerprint


def test_same_content_same_fingerprint(tmp_path):
    """Two physically distinct files with identical logical content share fingerprint."""
    a = tmp_path / "first.csv"
    b = tmp_path / "second.csv"
    rows = [(i, i * 2) for i in range(50)]
    _write(a, rows)
    _write(b, rows)

    storage = tmp_path / "data-index"
    storage.mkdir()
    index_local(path=str(a), name="dsA", storage_path=str(storage))
    index_local(path=str(b), name="dsB", storage_path=str(storage))

    store = DataStore(str(storage))
    fp_a = store.load("dsA").fingerprint
    fp_b = store.load("dsB").fingerprint
    assert fp_a == fp_b


def test_different_content_different_fingerprint(tmp_path):
    a = tmp_path / "first.csv"
    b = tmp_path / "second.csv"
    _write(a, [(1, 2)])
    _write(b, [(1, 99)])

    storage = tmp_path / "data-index"
    storage.mkdir()
    index_local(path=str(a), name="dsA", storage_path=str(storage))
    index_local(path=str(b), name="dsB", storage_path=str(storage))

    store = DataStore(str(storage))
    assert store.load("dsA").fingerprint != store.load("dsB").fingerprint
