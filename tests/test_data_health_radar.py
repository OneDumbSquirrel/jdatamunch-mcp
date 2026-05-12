"""Tests for data_health_radar + diff_data_health_radar (v1.11.0)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from jdatamunch_mcp.runtime.ingest import ingest_sql_log_file
from jdatamunch_mcp.tools.data_health_radar import data_health_radar
from jdatamunch_mcp.tools.health_radar import (
    compute_radar,
    diff_data_health_radar,
    diff_radar,
)
from jdatamunch_mcp.tools.index_local import index_local


def _index_users_csv(tmp_path: Path, name: str = "users") -> str:
    path = tmp_path / f"{name}.csv"
    rows = ["id,name,email,age"]
    for i in range(1, 21):
        nm = ["alice", "bob", "carol"][i % 3]
        rows.append(f"{i},{nm},{nm}@example.com,{20 + (i % 30)}")
    path.write_text("\n".join(rows) + "\n")
    storage = str(tmp_path / "store")
    res = index_local(path=str(path), use_ai_summaries=False, name=name, storage_path=storage)
    assert "error" not in res, f"index failed: {res}"
    return storage


def _index_dirty_csv(tmp_path: Path, name: str = "dirty") -> str:
    """Dataset with: heavy nulls in `notes`, no PK (all ids repeat), one
    constant column. Designed to score poorly."""
    path = tmp_path / f"{name}.csv"
    rows = ["id,category,notes,flag"]
    for i in range(20):
        # id repeats — no PK candidate
        # notes is 80% null
        # flag is constant "x"
        rid = i % 4
        notes = "" if i < 16 else f"note{i}"
        rows.append(f"{rid},A,{notes},x")
    path.write_text("\n".join(rows) + "\n")
    storage = str(tmp_path / "store")
    res = index_local(path=str(path), use_ai_summaries=False, name=name, storage_path=storage)
    assert "error" not in res, f"index failed: {res}"
    return storage


# --- compute_radar pure-function basics ---

def test_compute_radar_clean_data_scores_high():
    r = compute_radar(
        avg_null_pct=0.0,
        avg_type_confidence=1.0,
        constant_columns=0,
        total_columns=5,
        has_pk=True,
        typed_columns=3,
        typeable_candidates=3,
        drift_free=True,
    )
    assert r["composite"] >= 90.0
    assert r["grade"] == "A"
    assert r["omitted_axes"] == ["runtime_coverage"]


def test_compute_radar_dirty_data_scores_low():
    r = compute_radar(
        avg_null_pct=80.0,
        avg_type_confidence=0.5,
        constant_columns=4,
        total_columns=5,
        has_pk=False,
        typed_columns=0,
        typeable_candidates=5,
        drift_free=False,
    )
    assert r["composite"] < 50.0
    assert r["grade"] in ("D", "F")


def test_compute_radar_omits_history_when_none():
    r = compute_radar(
        avg_null_pct=0.0, avg_type_confidence=1.0,
        constant_columns=0, total_columns=5,
        has_pk=True, typed_columns=0, typeable_candidates=0,
        drift_free=None,
    )
    assert "schema_stability" in r["omitted_axes"]
    assert "schema_stability" not in r["axes"]


def test_compute_radar_includes_runtime_when_passed():
    r = compute_radar(
        avg_null_pct=0.0, avg_type_confidence=1.0,
        constant_columns=0, total_columns=5,
        has_pk=True, typed_columns=0, typeable_candidates=0,
        runtime_coverage_pct=75.0,
    )
    assert "runtime_coverage" in r["axes"]
    assert r["axes"]["runtime_coverage"]["score"] == 75.0
    assert "runtime_coverage" not in r["omitted_axes"]


# --- diff_radar ---

def test_diff_flags_regressions():
    baseline = compute_radar(
        avg_null_pct=0.0, avg_type_confidence=1.0,
        constant_columns=0, total_columns=5,
        has_pk=True, typed_columns=3, typeable_candidates=3,
        drift_free=True,
    )
    current = compute_radar(
        avg_null_pct=40.0, avg_type_confidence=1.0,
        constant_columns=0, total_columns=5,
        has_pk=True, typed_columns=3, typeable_candidates=3,
        drift_free=True,
    )
    d = diff_radar(baseline, current)
    assert "null_health" in d["regressions"]
    assert d["composite_delta"] < 0
    assert "REGRESSION" in d["verdict"]


def test_diff_no_change_verdict():
    baseline = compute_radar(
        avg_null_pct=0.0, avg_type_confidence=1.0,
        constant_columns=0, total_columns=5,
        has_pk=True, typed_columns=3, typeable_candidates=3,
        drift_free=True,
    )
    d = diff_radar(baseline, baseline)
    assert d["composite_delta"] == 0.0
    assert d["verdict"] == "no meaningful change"


def test_diff_data_health_radar_rejects_bad_input():
    r = diff_data_health_radar("not a dict", {"axes": {}})
    assert "error" in r

    r = diff_data_health_radar({"composite": 80.0}, {"composite": 90.0})
    assert "error" in r
    assert "axes" in r["error"].lower()


# --- data_health_radar integration ---

def test_unknown_dataset(tmp_path):
    r = data_health_radar("not_a_dataset", storage_path=str(tmp_path / "store"))
    assert "error" in r


def test_clean_dataset_scores_well(tmp_path):
    storage = _index_users_csv(tmp_path)
    r = data_health_radar("users", storage_path=storage)
    assert "result" in r
    radar = r["result"]["radar"]
    assert radar["composite"] >= 70.0
    assert radar["grade"] in ("A", "B", "C")
    assert "null_health" in radar["axes"]


def test_dirty_dataset_grades_lower_than_clean(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    clean = _index_users_csv(a)
    dirty = _index_dirty_csv(b)
    clean_r = data_health_radar("users", storage_path=clean)
    dirty_r = data_health_radar("dirty", storage_path=dirty)
    assert dirty_r["result"]["radar"]["composite"] < clean_r["result"]["radar"]["composite"]


def test_omits_runtime_axis_when_no_traces(tmp_path):
    storage = _index_users_csv(tmp_path)
    r = data_health_radar("users", storage_path=storage)
    assert "runtime_coverage" in r["result"]["radar"]["omitted_axes"]


def test_includes_runtime_axis_when_traces_present(tmp_path):
    storage = _index_users_csv(tmp_path)
    log_path = tmp_path / "log.csv"
    with open(log_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["query", "calls", "total_time"])
        w.writeheader()
        w.writerow({"query": "SELECT name, email FROM users WHERE id = 1",
                    "calls": "10", "total_time": "1"})
    ingest_sql_log_file(str(log_path), storage_path=storage)

    r = data_health_radar("users", storage_path=storage)
    radar = r["result"]["radar"]
    assert "runtime_coverage" in radar["axes"]
    assert "runtime_coverage" not in radar["omitted_axes"]
    # name + email + id (from WHERE clause) hit; age does not. Some
    # mix > 0 and < 100 — exact value depends on parser column extraction.
    assert 0.0 < radar["axes"]["runtime_coverage"]["raw"] < 100.0


def test_include_runtime_false_omits_axis(tmp_path):
    storage = _index_users_csv(tmp_path)
    log_path = tmp_path / "log.csv"
    with open(log_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["query", "calls", "total_time"])
        w.writeheader()
        w.writerow({"query": "SELECT name FROM users", "calls": "1", "total_time": "1"})
    ingest_sql_log_file(str(log_path), storage_path=storage)

    r = data_health_radar("users", include_runtime=False, storage_path=storage)
    assert "runtime_coverage" in r["result"]["radar"]["omitted_axes"]
