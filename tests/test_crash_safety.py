"""Crash-safe ingest: verify partial loads cannot leak (A4)."""

import csv
import multiprocessing
import os
import sqlite3
import time
from pathlib import Path

import pytest

from jdatamunch_mcp.storage.data_store import DataStore


def _slow_index_target(csv_path, storage_path, dataset_id):
    """Run index_local; killed externally by the parent process."""
    from jdatamunch_mcp.tools.index_local import index_local
    index_local(path=csv_path, name=dataset_id, storage_path=storage_path)


def test_lock_cleanup_after_simulated_crash(tmp_path):
    """Manually leaving a _lock + tmp file behind must not poison subsequent reads."""
    storage = tmp_path / "data-index"
    storage.mkdir()
    dataset_id = "victim"
    store = DataStore(base_path=str(storage))

    # Fabricate a half-finished index dir: lock + stale tmp, no index.json.
    d = store.dataset_dir(dataset_id)
    d.mkdir(parents=True, exist_ok=True)
    store.acquire_lock(dataset_id)
    (d / "data.sqlite.tmp").write_bytes(b"truncated")
    (d / "index.json.tmp").write_bytes(b"{")

    cleaned = store.cleanup_stale_artifacts(dataset_id)
    assert cleaned is True
    assert not store.lock_path(dataset_id).exists()
    assert not (d / "data.sqlite.tmp").exists()
    # load() must report no dataset present.
    assert store.load(dataset_id) is None


def test_successful_index_leaves_no_tmp(tmp_path):
    """A normal run must clean up data.sqlite.tmp + _lock."""
    csv_path = tmp_path / "tiny.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["a", "b"])
        for i in range(50):
            w.writerow([i, i * 2])

    storage = tmp_path / "data-index"
    storage.mkdir()
    from jdatamunch_mcp.tools.index_local import index_local
    result = index_local(path=str(csv_path), name="tiny", storage_path=str(storage))
    assert "error" not in result, result

    store = DataStore(base_path=str(storage))
    d = store.dataset_dir("tiny")
    assert not (d / "data.sqlite.tmp").exists()
    assert not store.lock_path("tiny").exists()
    assert store.sqlite_path("tiny").exists()
    assert store.index_path("tiny").exists()
    assert store.index_checksum_path("tiny").exists()
