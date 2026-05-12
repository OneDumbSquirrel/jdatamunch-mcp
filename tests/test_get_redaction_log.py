"""Tests for get_redaction_log (v1.10.0)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from jdatamunch_mcp.runtime.ingest import ingest_sql_log_file
from jdatamunch_mcp.tools.get_redaction_log import get_redaction_log
from jdatamunch_mcp.tools.index_local import index_local


def _index_users_csv(tmp_path: Path) -> str:
    csv_path = tmp_path / "users.csv"
    rows = ["id,name,email"]
    repeats = ["alice", "bob", "carol", "dave", "eve"]
    for i in range(1, 11):
        nm = repeats[(i - 1) % len(repeats)]
        rows.append(f"{i},{nm},{nm}@example.com")
    csv_path.write_text("\n".join(rows) + "\n")
    storage = str(tmp_path / "store")
    res = index_local(path=str(csv_path), use_ai_summaries=False, name="users", storage_path=storage)
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
    """Indexed dataset + a SQL log full of literals to scrub."""
    storage = _index_users_csv(tmp_path)
    log = _write_sql_log(tmp_path, [
        {"query": "SELECT name FROM users WHERE email = 'alice@example.com'",
         "calls": "5", "total_time": "1"},
        {"query": "SELECT email FROM users WHERE id = 42",
         "calls": "7", "total_time": "2"},
        {"query": "SELECT * FROM users WHERE name = 'bob' AND id = 99",
         "calls": "3", "total_time": "1"},
    ])
    ingest_sql_log_file(str(log), storage_path=storage)
    return storage


def test_returns_pattern_counts(populated):
    r = get_redaction_log("users", storage_path=populated)
    assert "error" not in r, f"unexpected error: {r}"
    assert r["dataset"] == "users"
    assert r["total_redactions"] > 0
    assert "sql_log" in r["sources"]

    by_pattern = {p["pattern"]: p for p in r["patterns"]}
    # The numeric literals (1, 42, 99) and string literals should both fire.
    assert any(k for k in by_pattern if "numeric" in k or "string" in k)
    for p in r["patterns"]:
        assert p["count"] > 0
        assert p["source"] == "sql_log"
        assert p["last_seen"]


def test_filter_by_source(populated):
    r = get_redaction_log("users", source="sql_log", storage_path=populated)
    assert "error" not in r
    assert r["sources"] == ["sql_log"]
    assert r["_meta"]["filter_source"] == "sql_log"


def test_invalid_source_rejected(populated):
    r = get_redaction_log("users", source="otel", storage_path=populated)
    assert "error" in r
    assert r["reason"] == "invalid_source"


def test_unknown_dataset(tmp_path):
    r = get_redaction_log("not_a_real_dataset", storage_path=str(tmp_path / "store"))
    assert "error" in r
    assert r["reason"] == "dataset_not_found"


def test_empty_when_no_ingest(tmp_path):
    """Empty patterns is NOT an error — it's a valid 'nothing scrubbed yet' state."""
    storage = _index_users_csv(tmp_path)
    r = get_redaction_log("users", storage_path=storage)
    assert "error" not in r
    assert r["patterns"] == []
    assert r["total_redactions"] == 0
    assert r["sources"] == []
