"""Tests for find_unused_columns (v1.6.0)."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jdatamunch_mcp.runtime.ingest import ingest_sql_log_file
from jdatamunch_mcp.runtime.tables import ensure_runtime_tables
from jdatamunch_mcp.storage.data_store import DataStore
from jdatamunch_mcp.tools.find_unused_columns import find_unused_columns
from jdatamunch_mcp.tools.index_local import index_local


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

def _index_users_csv(tmp_path: Path, name: str = "users") -> str:
    """Index a users dataset (10 rows — enough to defeat tiny-table PK
    inference on the categorical columns)."""
    csv_path = tmp_path / f"{name}.csv"
    rows = ["id,name,email,active,created_at,updated_at,unused_legacy"]
    # Names and emails repeat so they don't read as PK candidates;
    # `id` is the only unique column → only PK candidate.
    repeats = ["alice", "bob", "carol", "dave", "eve"]
    for i in range(1, 11):
        active = "true" if i % 2 == 0 else "false"
        nm = repeats[(i - 1) % len(repeats)]
        em = f"{nm}@example.com"
        rows.append(f"{i},{nm},{em},{active},2024-01-0{i if i < 10 else 9},2024-01-1{i % 10},")
    csv_path.write_text("\n".join(rows) + "\n")
    storage = str(tmp_path / "store")
    res = index_local(path=str(csv_path), use_ai_summaries=False, name=name, storage_path=storage)
    assert "error" not in res, f"index failed: {res}"
    return storage


def _write_sql_log(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "log.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


@pytest.fixture
def populated(tmp_path):
    """Indexed dataset + SQL log ingested. Queries hit `name` and `email`
    but not `active` or `unused_legacy`."""
    storage = _index_users_csv(tmp_path)
    log = _write_sql_log(tmp_path, [
        {"query": "SELECT name FROM users WHERE id = 1", "calls": "10", "total_time": "1"},
        {"query": "SELECT email FROM users", "calls": "20", "total_time": "2"},
    ])
    ingest_sql_log_file(str(log), storage_path=storage)
    return storage


# --------------------------------------------------------------------------- #
# Refusal path                                                                #
# --------------------------------------------------------------------------- #

def test_refuses_when_no_runtime_data(tmp_path):
    storage = _index_users_csv(tmp_path)
    r = find_unused_columns("users", storage_path=storage)
    assert "error" in r
    assert r["reason"] == "refused_no_runtime_data"
    assert "ingest_sql_log" in r["hint"]


def test_unknown_dataset(tmp_path):
    r = find_unused_columns("not_a_real_dataset", storage_path=str(tmp_path / "store"))
    assert "error" in r
    assert r["reason"] == "dataset_not_found"


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #

def test_finds_unused_columns(populated):
    r = find_unused_columns("users", storage_path=populated)
    assert "result" in r, f"unexpected response: {r}"
    res = r["result"]
    cols = {u["column"].lower() for u in res["unused"]}
    # 'active' and 'unused_legacy' should be flagged; 'name' and 'email' should not
    assert "active" in cols
    assert "unused_legacy" in cols
    assert "name" not in cols
    assert "email" not in cols


def test_used_columns_not_flagged(populated):
    r = find_unused_columns("users", storage_path=populated)
    used = {u["column"].lower() for u in r["result"]["unused"]}
    # name and email had calls — should be absent from the unused list
    assert "name" not in used
    assert "email" not in used


def test_pk_excluded_by_default(populated):
    r = find_unused_columns("users", storage_path=populated)
    cols = {u["column"].lower() for u in r["result"]["unused"]}
    # `id` is a primary-key candidate (all unique, integer) — should be skipped
    assert "id" not in cols
    assert r["result"]["skipped_pk"] >= 1


def test_audit_fields_excluded_by_default(populated):
    r = find_unused_columns("users", storage_path=populated)
    cols = {u["column"].lower() for u in r["result"]["unused"]}
    assert "created_at" not in cols
    assert "updated_at" not in cols
    assert r["result"]["skipped_audit"] >= 2


def test_exclude_audit_false_includes_them(populated):
    r = find_unused_columns("users", exclude_audit=False, storage_path=populated)
    cols = {u["column"].lower() for u in r["result"]["unused"]}
    # With audit exclusion off, created_at + updated_at appear (they have no runtime hits)
    assert "created_at" in cols
    assert "updated_at" in cols


def test_exclude_pk_false_includes_id(populated):
    r = find_unused_columns("users", exclude_pk=False, storage_path=populated)
    cols = {u["column"].lower() for u in r["result"]["unused"]}
    # `id` had a hit in WHERE clause — should NOT be flagged as unused
    # (it does appear in runtime data even without the PK exclusion)
    assert "id" not in cols


def test_reason_classifications(populated):
    r = find_unused_columns("users", storage_path=populated)
    reasons = {u["column"].lower(): u["reason"] for u in r["result"]["unused"]}
    # Both should be zero_hits since they never appear in any query
    assert reasons.get("active") == "zero_hits"
    assert reasons.get("unused_legacy") == "zero_hits"


def test_min_calls_floor(populated):
    """With min_calls=15, columns with <15 hits in window are flagged."""
    r = find_unused_columns("users", min_calls=15, storage_path=populated)
    cols_by_reason: dict[str, list[str]] = {}
    for u in r["result"]["unused"]:
        cols_by_reason.setdefault(u["reason"], []).append(u["column"].lower())
    # name had 10 calls (< 15) → below_min_calls; email had 20 → not flagged
    assert "name" in cols_by_reason.get("below_min_calls", [])
    assert "email" not in cols_by_reason.get("below_min_calls", [])


def test_stale_classification(tmp_path):
    """Pre-seed an old last_seen so a column shows as stale."""
    storage = _index_users_csv(tmp_path)
    # Manually insert an old runtime_query_calls row
    store = DataStore(base_path=storage)
    conn = sqlite3.connect(str(store.sqlite_path("users")))
    ensure_runtime_tables(conn)
    long_ago = (datetime.now(tz=timezone.utc) - timedelta(days=400)).isoformat()
    conn.execute(
        """INSERT INTO runtime_query_calls
           (query_fingerprint, table_ref, column_ref, calls, total_time_ms, first_seen, last_seen, source)
           VALUES (?, 'users', 'active', 5, 1.0, ?, ?, 'sql_log')""",
        ("sha256:old", long_ago, long_ago),
    )
    conn.commit()
    conn.close()
    r = find_unused_columns("users", window_days=30, exclude_audit=False, storage_path=storage)
    by_col = {u["column"].lower(): u for u in r["result"]["unused"]}
    assert by_col["active"]["reason"] == "stale"
    assert by_col["active"]["last_seen"] is not None


def test_window_days_filter(tmp_path):
    """A wider window should include calls a narrow window excluded."""
    storage = _index_users_csv(tmp_path)
    store = DataStore(base_path=storage)
    conn = sqlite3.connect(str(store.sqlite_path("users")))
    ensure_runtime_tables(conn)
    forty_days_ago = (datetime.now(tz=timezone.utc) - timedelta(days=40)).isoformat()
    conn.execute(
        """INSERT INTO runtime_query_calls
           (query_fingerprint, table_ref, column_ref, calls, total_time_ms, first_seen, last_seen, source)
           VALUES (?, 'users', 'active', 5, 1.0, ?, ?, 'sql_log')""",
        ("sha256:mid", forty_days_ago, forty_days_ago),
    )
    conn.commit()
    conn.close()
    narrow = find_unused_columns("users", window_days=30, exclude_audit=False, storage_path=storage)
    wide = find_unused_columns("users", window_days=90, exclude_audit=False, storage_path=storage)
    narrow_active = next(u for u in narrow["result"]["unused"] if u["column"].lower() == "active")
    assert narrow_active["reason"] == "stale"
    # 90-day window should NOT flag active (calls within window)
    wide_active = [u for u in wide["result"]["unused"] if u["column"].lower() == "active"]
    assert wide_active == [] or wide_active[0]["reason"] != "stale"


def test_result_shape(populated):
    r = find_unused_columns("users", storage_path=populated)
    res = r["result"]
    assert "dataset" in res
    assert "total_columns" in res
    assert "evaluated" in res
    assert "unused" in res
    assert "window_days" in res
    for u in res["unused"]:
        for k in ("table", "column", "calls_in_window", "last_seen", "fk_status", "type", "reason"):
            assert k in u, f"missing {k!r} in {u}"


def test_evaluated_count_consistent(populated):
    r = find_unused_columns("users", storage_path=populated)
    res = r["result"]
    # total = evaluated + skipped_pk + skipped_audit
    assert res["total_columns"] == res["evaluated"] + res["skipped_pk"] + res["skipped_audit"]
