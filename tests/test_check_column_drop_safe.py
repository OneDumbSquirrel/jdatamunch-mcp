"""Tests for check_column_drop_safe (v1.8.0)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from jdatamunch_mcp.runtime.ingest import ingest_sql_log_file
from jdatamunch_mcp.tools.check_column_drop_safe import check_column_drop_safe
from jdatamunch_mcp.tools.index_local import index_local


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def _index(tmp_path: Path, dataset: str, header: str, rows: list[str]) -> str:
    csv_path = tmp_path / f"{dataset}.csv"
    _write_csv(csv_path, header, rows)
    storage = str(tmp_path / "store")
    res = index_local(
        path=str(csv_path), use_ai_summaries=False,
        name=dataset, storage_path=storage,
    )
    assert "error" not in res, f"index failed: {res}"
    return storage


def _write_log(tmp_path: Path, queries: list[tuple[str, int]]) -> Path:
    path = tmp_path / "log.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["query", "calls", "total_time"])
        w.writeheader()
        for q, calls in queries:
            w.writerow({"query": q, "calls": str(calls), "total_time": "1.0"})
    return path


# --------------------------------------------------------------------------- #
# Error paths                                                                 #
# --------------------------------------------------------------------------- #

def test_unknown_dataset(tmp_path):
    r = check_column_drop_safe("nope", "x", storage_path=str(tmp_path / "store"))
    assert "error" in r
    assert r["reason"] == "dataset_not_found"


def test_unknown_column(tmp_path):
    storage = _index(tmp_path, "users", "id,name", [f"{i},user{i}" for i in range(1, 11)])
    r = check_column_drop_safe("users", "no_such_column", storage_path=storage)
    assert "error" in r
    assert r["reason"] == "column_not_found"


# --------------------------------------------------------------------------- #
# Channel 1: PK status                                                        #
# --------------------------------------------------------------------------- #

def test_pk_blocking(tmp_path):
    storage = _index(
        tmp_path, "users",
        "id,name",
        [f"{i},user{i % 3}" for i in range(1, 11)],  # unique id, repeating names
    )
    r = check_column_drop_safe("users", "id", storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "pk_blocking"
    assert any(b["kind"] == "pk_candidate" for b in res["blockers"])
    assert res["evidence"]["is_primary_key_candidate"] is True


# --------------------------------------------------------------------------- #
# Channel 4: Runtime traffic                                                  #
# --------------------------------------------------------------------------- #

def test_runtime_observed(tmp_path):
    # Repeated emails so the column is not a PK candidate
    storage = _index(
        tmp_path, "users",
        "id,name,email",
        [f"{i},alice,shared@x.com" for i in range(1, 11)],
    )
    log = _write_log(tmp_path, [
        ("SELECT email FROM users WHERE id = 1", 5),
    ])
    ingest_sql_log_file(str(log), storage_path=storage)
    r = check_column_drop_safe("users", "email", storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "runtime_observed", f"got {res['verdict']} with blockers={res['blockers']}"
    assert res["evidence"]["runtime_calls_in_window"] >= 5
    assert any(b["kind"] == "runtime_observed" for b in res["blockers"])


def test_window_days_filter(tmp_path):
    storage = _index(
        tmp_path, "users",
        "id,name,email",
        [f"{i},alice,shared@x.com" for i in range(1, 11)],
    )
    log = _write_log(tmp_path, [("SELECT email FROM users", 3)])
    ingest_sql_log_file(str(log), storage_path=storage)
    r = check_column_drop_safe("users", "email", window_days=365, storage_path=storage)
    assert r["result"]["verdict"] == "runtime_observed"


# --------------------------------------------------------------------------- #
# Channel 5: Cross-dataset name match                                         #
# --------------------------------------------------------------------------- #

def test_cross_dataset_name_match(tmp_path):
    storage = _index(tmp_path, "users", "id,email",
                     [f"{i},u{i}@x.com" for i in range(1, 11)])
    # Second dataset shares the same storage; both have a 'email' column.
    csv2 = tmp_path / "contacts.csv"
    _write_csv(csv2, "id,email,phone",
               [f"{i},c{i}@x.com,555-{i:04d}" for i in range(1, 11)])
    res2 = index_local(path=str(csv2), use_ai_summaries=False,
                       name="contacts", storage_path=storage)
    assert "error" not in res2

    r = check_column_drop_safe("users", "email", storage_path=storage)
    res = r["result"]
    # email isn't PK / FK / runtime-observed → cross_dataset_blocking
    assert "contacts" in res["evidence"]["cross_dataset_name_matches"]
    assert any(b["kind"] == "cross_dataset_name_match" for b in res["blockers"])


# --------------------------------------------------------------------------- #
# Channel 2/3: FK heuristics                                                  #
# --------------------------------------------------------------------------- #

def test_fk_source_name_match(tmp_path):
    """orders.user_id → users.id via stem-match (orders.user_id → 'users' has PK 'id')."""
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    # user_id repeats so it isn't a PK candidate itself
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    res2 = index_local(path=str(csv2), use_ai_summaries=False,
                       name="orders", storage_path=storage)
    assert "error" not in res2

    r = check_column_drop_safe("orders", "user_id", storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "fk_blocking", f"got {res['verdict']}"
    assert res["evidence"]["fk_source"] is not None
    assert res["evidence"]["fk_source"]["target_dataset"] == "users"


def test_fk_target_when_pk_is_referenced(tmp_path):
    """users.id is PK; orders.user_id likely references it."""
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    # user_id repeats so it isn't a PK candidate itself
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)

    r = check_column_drop_safe("users", "id", storage_path=storage)
    res = r["result"]
    # PK blocking takes precedence over FK target, but the FK target evidence still surfaces
    assert res["verdict"] == "pk_blocking"
    fk_refs = res["evidence"].get("fk_referers") or []
    assert any(ref["source_dataset"] == "orders" for ref in fk_refs)


# --------------------------------------------------------------------------- #
# Safe path                                                                   #
# --------------------------------------------------------------------------- #

def test_safe_to_drop_with_runtime(tmp_path):
    """Column with no PK / FK / runtime / cross-match → safe_to_drop.

    Runtime data must be present, otherwise the response notes the
    static-only caveat in recommended_action."""
    storage = _index(
        tmp_path, "users",
        "id,name,legacy_flag",
        # legacy_flag has repeating values so it's not PK-candidate
        [f"{i},alice,old" for i in range(1, 11)],
    )
    # Ingest a log that does NOT touch legacy_flag
    log = _write_log(tmp_path, [("SELECT name FROM users", 5)])
    ingest_sql_log_file(str(log), storage_path=storage)
    r = check_column_drop_safe("users", "legacy_flag", storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "safe_to_drop", f"got {res['verdict']} with blockers={res['blockers']}"
    assert res["blockers"] == []


def test_safe_with_no_runtime_warns(tmp_path):
    """Without runtime data, even safe_to_drop verdicts surface a hint."""
    storage = _index(
        tmp_path, "users",
        "id,name,legacy_flag",
        [f"{i},alice,old" for i in range(1, 11)],
    )
    r = check_column_drop_safe("users", "legacy_flag", storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "safe_to_drop"
    assert res["evidence"]["runtime_data_present"] is False
    assert "ingest_sql_log" in res["recommended_action"]


# --------------------------------------------------------------------------- #
# Response shape                                                              #
# --------------------------------------------------------------------------- #

def test_response_shape(tmp_path):
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    r = check_column_drop_safe("users", "name", storage_path=storage)
    res = r["result"]
    for k in ("dataset", "column", "verdict", "blockers", "evidence", "recommended_action"):
        assert k in res
    for k in ("is_primary_key_candidate", "fk_source", "runtime_data_present",
              "runtime_calls_in_window", "runtime_last_seen",
              "cross_dataset_name_matches"):
        assert k in res["evidence"], f"missing {k}"
    assert "channels_fired" in r["_meta"]


def test_blocker_cap(tmp_path):
    """Pile up channels and confirm the cap holds."""
    # users.id is PK + has FK referers + runtime-observed + cross-named
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    _write_csv(csv2, "order_id,user_id,id",
               [f"{i*100},{(i % 10) + 1},{i}" for i in range(1, 11)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)
    log = _write_log(tmp_path, [("SELECT id FROM users", 5)])
    ingest_sql_log_file(str(log), storage_path=storage)

    r = check_column_drop_safe("users", "id", storage_path=storage)
    res = r["result"]
    assert len(res["blockers"]) <= 5
