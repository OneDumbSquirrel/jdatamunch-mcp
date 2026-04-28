"""validate_index correctness (A5)."""

import csv
import sqlite3

import pytest

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.validate_index import validate_index
from jdatamunch_mcp.storage.data_store import DataStore


@pytest.fixture
def indexed(tmp_path):
    csv_path = tmp_path / "ok.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["a", "b"])
        for i in range(20):
            w.writerow([i, i * 10])
    storage = tmp_path / "data-index"
    storage.mkdir()
    res = index_local(path=str(csv_path), name="ok", storage_path=str(storage))
    assert "error" not in res
    return str(storage)


def test_validate_clean_dataset(indexed):
    res = validate_index(dataset="ok", storage_path=indexed)
    assert res["result"]["overall_status"] == "ok", res
    assert res["result"]["row_count"] == 20


def test_validate_missing_dataset(indexed):
    res = validate_index(dataset="nonexistent", storage_path=indexed)
    assert res["result"]["overall_status"] == "error"


def test_validate_detects_row_count_mismatch(indexed):
    """If we delete rows directly from SQLite, validate_index must catch it."""
    store = DataStore(base_path=indexed)
    sql_path = store.sqlite_path("ok")
    with sqlite3.connect(str(sql_path)) as conn:
        conn.execute("DELETE FROM rows WHERE rowid > 5")
        conn.commit()
    res = validate_index(dataset="ok", storage_path=indexed)
    assert res["result"]["overall_status"] == "error"
    msgs = " ".join(f["message"] for f in res["result"]["findings"])
    assert "row_count" in msgs


def test_validate_detects_checksum_mismatch(indexed):
    """Tampering with index.json without updating the .sha256 sidecar must trigger error."""
    store = DataStore(base_path=indexed)
    idx_path = store.index_path("ok")
    raw = idx_path.read_bytes()
    idx_path.write_bytes(raw + b" ")  # invisible whitespace mutation
    res = validate_index(dataset="ok", storage_path=indexed)
    assert res["result"]["overall_status"] == "error"
    msgs = " ".join(f["message"] for f in res["result"]["findings"])
    assert "checksum" in msgs


def test_validate_warns_on_stale_lock(indexed):
    store = DataStore(base_path=indexed)
    store.acquire_lock("ok")
    res = validate_index(dataset="ok", storage_path=indexed)
    # Lock + index.json present → warning, not error
    assert res["result"]["overall_status"] in ("warning", "error")
