"""Tests for get_schema_impact (v1.9.0)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from jdatamunch_mcp.runtime.ingest import ingest_sql_log_file
from jdatamunch_mcp.tools.get_schema_impact import get_schema_impact
from jdatamunch_mcp.tools.index_local import index_local


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def _index(tmp_path: Path, dataset: str, header: str, rows: list[str], storage: str = None) -> str:
    csv_path = tmp_path / f"{dataset}.csv"
    _write_csv(csv_path, header, rows)
    storage = storage or str(tmp_path / "store")
    res = index_local(path=str(csv_path), use_ai_summaries=False,
                      name=dataset, storage_path=storage)
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
    r = get_schema_impact("nope", "x", storage_path=str(tmp_path / "store"))
    assert "error" in r and r["reason"] == "dataset_not_found"


def test_unknown_column(tmp_path):
    storage = _index(tmp_path, "users", "id,name", [f"{i},u{i}" for i in range(1, 11)])
    r = get_schema_impact("users", "nope", storage_path=storage)
    assert "error" in r and r["reason"] == "column_not_found"


def test_invalid_kind(tmp_path):
    storage = _index(tmp_path, "users", "id,name", [f"{i},u{i}" for i in range(1, 11)])
    r = get_schema_impact("users", "name", kind="alter_column", storage_path=storage)
    assert "error" in r and r["reason"] == "invalid_kind"


def test_rename_requires_new_name(tmp_path):
    storage = _index(tmp_path, "users", "id,name", [f"{i},u{i}" for i in range(1, 11)])
    r = get_schema_impact("users", "name", kind="rename_column", storage_path=storage)
    assert "error" in r and r["reason"] == "missing_new_name"


def test_retype_requires_new_type(tmp_path):
    storage = _index(tmp_path, "users", "id,name", [f"{i},u{i}" for i in range(1, 11)])
    r = get_schema_impact("users", "name", kind="retype_column", storage_path=storage)
    assert "error" in r and r["reason"] == "missing_new_type"


# --------------------------------------------------------------------------- #
# Isolated dataset — no FK / cross-dataset / runtime → safe                    #
# --------------------------------------------------------------------------- #

def test_isolated_drop_is_safe(tmp_path):
    storage = _index(tmp_path, "users",
                     "id,name,legacy_flag",
                     [f"{i},alice,old" for i in range(1, 11)])
    r = get_schema_impact("users", "legacy_flag", storage_path=storage)
    res = r["result"]
    assert res["direct_impact"] == []
    assert res["transitive_impact"] == []
    assert res["summary"]["fk_edges_broken"] == 0
    assert res["blast_score"] == 0.0


# --------------------------------------------------------------------------- #
# FK edges                                                                    #
# --------------------------------------------------------------------------- #

def test_fk_source_drop_flags_target(tmp_path):
    """orders.user_id → users.id; dropping orders.user_id breaks the edge."""
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)

    r = get_schema_impact("orders", "user_id", storage_path=storage)
    res = r["result"]
    assert res["summary"]["fk_edges_broken"] >= 1
    kinds = {d["kind"] for d in res["direct_impact"]}
    assert "fk_source" in kinds
    targets = [d for d in res["direct_impact"] if d["kind"] == "fk_source"]
    assert any(t["target_dataset"] == "users" for t in targets)


def test_pk_drop_flags_fk_sources(tmp_path):
    """users.id is PK; orders.user_id references it. Dropping users.id surfaces orders."""
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)

    r = get_schema_impact("users", "id", storage_path=storage)
    res = r["result"]
    kinds = {d["kind"] for d in res["direct_impact"]}
    assert "fk_target" in kinds
    targets = [d for d in res["direct_impact"] if d["kind"] == "fk_target"]
    assert any(t["dataset"] == "orders" for t in targets)


# --------------------------------------------------------------------------- #
# Cross-dataset name match                                                    #
# --------------------------------------------------------------------------- #

def test_cross_dataset_match(tmp_path):
    storage = _index(tmp_path, "users", "id,email,name",
                     [f"{i},u{i}@x.com,alice" for i in range(1, 11)])
    csv2 = tmp_path / "contacts.csv"
    _write_csv(csv2, "id,email,phone",
               [f"{i},c{i}@x.com,555-{i:04d}" for i in range(1, 11)])
    index_local(path=str(csv2), use_ai_summaries=False, name="contacts", storage_path=storage)

    r = get_schema_impact("users", "email", storage_path=storage)
    res = r["result"]
    cross = [d for d in res["direct_impact"] if d["kind"] == "cross_dataset_name_match"]
    assert any(d["dataset"] == "contacts" for d in cross)


# --------------------------------------------------------------------------- #
# Runtime traffic surfaces                                                    #
# --------------------------------------------------------------------------- #

def test_runtime_traffic_in_direct(tmp_path):
    storage = _index(tmp_path, "users",
                     "id,name,email",
                     [f"{i},alice,shared@x.com" for i in range(1, 11)])
    log = _write_log(tmp_path, [("SELECT email FROM users", 7)])
    ingest_sql_log_file(str(log), storage_path=storage)
    r = get_schema_impact("users", "email", storage_path=storage)
    res = r["result"]
    runtime_hits = [d for d in res["direct_impact"] if d["kind"] == "runtime_traffic"]
    assert runtime_hits
    assert runtime_hits[0]["calls_in_window"] >= 7


# --------------------------------------------------------------------------- #
# Retype + type mismatch                                                      #
# --------------------------------------------------------------------------- #

def test_retype_to_compatible_no_mismatch(tmp_path):
    """integer → integer is compatible — no mismatch fired."""
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)

    r = get_schema_impact(
        "orders", "user_id",
        kind="retype_column", new_type="integer",
        storage_path=storage,
    )
    res = r["result"]
    assert res["summary"]["type_mismatches"] == []


def test_retype_to_incompatible_flags_mismatch(tmp_path):
    """integer → string is not compatible — mismatch entries appear."""
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)

    r = get_schema_impact(
        "orders", "user_id",
        kind="retype_column", new_type="string",
        storage_path=storage,
    )
    res = r["result"]
    assert res["summary"]["type_mismatches"], f"expected type_mismatches, got {res['summary']}"
    # The flagged edge should mention both datasets
    edge = res["summary"]["type_mismatches"][0]["edge"]
    assert "orders.user_id" in edge and "users" in edge


# --------------------------------------------------------------------------- #
# Rename — recommended_action mentions the new name                           #
# --------------------------------------------------------------------------- #

def test_rename_action_text(tmp_path):
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    r = get_schema_impact(
        "users", "name",
        kind="rename_column", new_name="full_name",
        storage_path=storage,
    )
    res = r["result"]
    assert "full_name" in res["recommended_action"]


# --------------------------------------------------------------------------- #
# Blast score is normalised                                                   #
# --------------------------------------------------------------------------- #

def test_blast_score_bounded(tmp_path):
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)

    r = get_schema_impact("users", "id", storage_path=storage)
    score = r["result"]["blast_score"]
    assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# Response shape                                                              #
# --------------------------------------------------------------------------- #

def test_response_shape(tmp_path):
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    r = get_schema_impact("users", "name", storage_path=storage)
    res = r["result"]
    for k in ("dataset", "column", "change", "direct_impact", "transitive_impact",
              "summary", "blast_score", "recommended_action"):
        assert k in res
    for k in ("datasets_affected", "direct_count", "transitive_count",
              "fk_edges_broken", "runtime_calls_in_window", "type_mismatches",
              "cross_dataset_name_matches"):
        assert k in res["summary"]


# --------------------------------------------------------------------------- #
# max_depth bounding                                                          #
# --------------------------------------------------------------------------- #

def test_max_depth_one_no_transitive(tmp_path):
    storage = _index(tmp_path, "users", "id,name",
                     [f"{i},u{i}" for i in range(1, 11)])
    csv2 = tmp_path / "orders.csv"
    _write_csv(csv2, "order_id,user_id,total",
               [f"{i*100},{(i % 4) + 1},{i * 10}" for i in range(1, 21)])
    index_local(path=str(csv2), use_ai_summaries=False, name="orders", storage_path=storage)

    r = get_schema_impact("users", "id", max_depth=1, storage_path=storage)
    assert r["result"]["transitive_impact"] == []
