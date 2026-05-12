"""Tests for get_data_hotspots tool."""

import csv
import pytest
from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.get_data_hotspots import get_data_hotspots


@pytest.fixture
def clean_csv(tmp_path):
    """Dataset with no data quality issues."""
    path = tmp_path / "clean.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "score"])
        for i in range(20):
            w.writerow([i, f"Person{i}", round(5.0 + (i % 5) * 0.5, 1)])
    return str(path)


@pytest.fixture
def dirty_csv(tmp_path):
    """Dataset with quality issues: heavy nulls, extreme spread."""
    path = tmp_path / "dirty.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "amount", "notes"])
        for i in range(20):
            category = "A" if i % 3 == 0 else ("B" if i % 3 == 1 else "C")
            # 'amount' has extreme outliers: mostly near 10, but one is 100000
            amount = 100000 if i == 0 else 10 + i
            # 'notes' is 75% null
            notes = None if i < 15 else f"note{i}"
            w.writerow([i, category, amount, notes])
    return str(path)


def _index(tmp_path, path, name):
    storage = str(tmp_path / "store")
    index_local(path=path, name=name, storage_path=storage)
    return storage


# --- Error cases ---

def test_not_indexed(tmp_path):
    storage = str(tmp_path / "store")
    r = get_data_hotspots("nonexistent", storage_path=storage)
    assert "error" in r
    assert "NOT_INDEXED" in r["error"]


# --- Basic structure ---

def test_result_structure(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", storage_path=storage)
    assert "result" in r
    res = r["result"]
    assert "dataset" in res
    assert "total_columns" in res
    assert "high_risk_columns" in res
    assert "medium_risk_columns" in res
    assert "overall_assessment" in res
    assert "hotspots" in res


def test_hotspot_entries(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", storage_path=storage)
    for h in r["result"]["hotspots"]:
        assert "column" in h
        assert "type" in h
        assert "hotspot_score" in h
        assert h["assessment"] in ("low", "medium", "high")
        assert "null_pct" in h
        assert "cardinality" in h


def test_returns_all_columns_by_default(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", top_n=50, storage_path=storage)
    # clean.csv has 3 columns; top_n=50 should return all of them
    assert r["result"]["total_columns"] == 3
    assert len(r["result"]["hotspots"]) == 3


# --- Top-N capping ---

def test_top_n_limits_results(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", top_n=1, storage_path=storage)
    assert len(r["result"]["hotspots"]) == 1


def test_top_n_cap_at_50(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", top_n=999, storage_path=storage)
    # Should not error; result capped at min(50, total_columns)
    assert len(r["result"]["hotspots"]) <= 50


# --- Ranking ---

def test_hotspots_sorted_descending(tmp_path, dirty_csv):
    storage = _index(tmp_path, dirty_csv, "dirty")
    r = get_data_hotspots("dirty", storage_path=storage)
    scores = [h["hotspot_score"] for h in r["result"]["hotspots"]]
    assert scores == sorted(scores, reverse=True)


def test_null_heavy_column_ranks_high(tmp_path, dirty_csv):
    storage = _index(tmp_path, dirty_csv, "dirty")
    r = get_data_hotspots("dirty", storage_path=storage)
    # 'notes' is 75% null — should appear in top results
    top_names = [h["column"] for h in r["result"]["hotspots"][:3]]
    assert "notes" in top_names


# --- Assessment ---

def test_assessment_values(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", storage_path=storage)
    assert r["result"]["overall_assessment"] in ("low", "medium", "high")


def test_high_null_triggers_high_or_medium(tmp_path, dirty_csv):
    storage = _index(tmp_path, dirty_csv, "dirty")
    r = get_data_hotspots("dirty", storage_path=storage)
    # dirty dataset has a 75%-null column — overall should be at least medium
    assert r["result"]["overall_assessment"] in ("medium", "high")


# --- Numeric extras ---

def test_numeric_columns_have_stats(tmp_path, dirty_csv):
    storage = _index(tmp_path, dirty_csv, "dirty")
    r = get_data_hotspots("dirty", storage_path=storage)
    numeric = [h for h in r["result"]["hotspots"] if h["type"] in ("integer", "float")]
    for h in numeric:
        assert "min" in h
        assert "max" in h
        assert "mean" in h


# --- Meta ---

def test_meta_present(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", storage_path=storage)
    assert "_meta" in r
    assert "timing_ms" in r["_meta"]
    assert "tokens_saved" in r["_meta"]


# --- v2 runtime fusion (v1.10.0) ---

import csv as _csv

from jdatamunch_mcp.runtime.ingest import ingest_sql_log_file


def _write_sql_log(tmp_path, rows):
    path = tmp_path / "log.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def test_no_runtime_data_emits_honest_hint(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", storage_path=storage)
    assert "runtime_caveat" in r["_meta"]
    assert "ingest_sql_log" in r["_meta"]["runtime_caveat"]
    assert r["_meta"]["signals_used"] == ["null_pct", "cardinality", "outlier"]
    assert r["result"]["runtime_data_present"] is False


def test_include_runtime_false_omits_caveat(tmp_path, clean_csv):
    storage = _index(tmp_path, clean_csv, "clean")
    r = get_data_hotspots("clean", include_runtime=False, storage_path=storage)
    assert "runtime_caveat" not in r["_meta"]
    assert r["_meta"]["signals_used"] == ["null_pct", "cardinality", "outlier"]


def test_traffic_signal_fuses_when_traces_exist(tmp_path, dirty_csv):
    storage = _index(tmp_path, dirty_csv, "dirty")
    log = _write_sql_log(tmp_path, [
        {"query": "SELECT amount FROM dirty WHERE id = 1", "calls": "1000", "total_time": "1"},
        {"query": "SELECT category FROM dirty", "calls": "5", "total_time": "1"},
    ])
    ingest_sql_log_file(str(log), storage_path=storage)

    r = get_data_hotspots("dirty", storage_path=storage)
    assert "runtime_caveat" not in r["_meta"]
    assert r["_meta"]["signals_used"] == ["null_pct", "cardinality", "outlier", "traffic"]
    assert r["result"]["runtime_data_present"] is True

    by_col = {h["column"]: h for h in r["result"]["hotspots"]}
    assert by_col["amount"]["traffic_calls"] >= 1000
    assert by_col["amount"]["traffic_score"] == 1.0
    # Heavily-trafficked outlier column should outrank lightly-trafficked category.
    assert by_col["amount"]["hotspot_score"] > by_col["category"]["hotspot_score"]
