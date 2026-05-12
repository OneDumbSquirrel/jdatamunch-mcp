"""Tests for runtime SQL-log ingest (v1.6.0)."""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from jdatamunch_mcp.redact import redact_sql_query_text, redact_trace_message
from jdatamunch_mcp.runtime.sql_log import (
    extract_columns,
    extract_tables,
    parse_sql_log_file,
)
from jdatamunch_mcp.runtime.ingest import ingest_sql_log_file
from jdatamunch_mcp.tools.index_local import index_local


# --------------------------------------------------------------------------- #
# Redaction extensions                                                        #
# --------------------------------------------------------------------------- #

class TestRedactSqlQueryText:
    def test_string_literal_scrubbed(self):
        q = "SELECT * FROM users WHERE email = 'alice@example.com'"
        out, summary = redact_sql_query_text(q)
        assert "'?'" in out
        assert "alice@example.com" not in out
        assert summary["patterns_matched"]["sql_string_literal"] >= 1

    def test_numeric_literal_scrubbed(self):
        q = "SELECT * FROM orders WHERE total > 100.50 AND id = 42"
        out, summary = redact_sql_query_text(q)
        assert "100.50" not in out
        assert "42" not in out
        assert summary["patterns_matched"]["sql_numeric_literal"] >= 2

    def test_identifier_digits_preserved(self):
        """Numeric scrubbing must not touch digits inside identifiers."""
        q = "SELECT col_123 FROM users_v2"
        out, _ = redact_sql_query_text(q)
        # col_123 and users_v2 must survive intact.
        assert "col_123" in out
        assert "users_v2" in out

    def test_pii_in_residue_still_caught(self):
        """Cell registry runs on what's left after literal scrubbing.

        We seed an unquoted JWT (rare in production but defensible —
        scripts sometimes paste tokens into comments)."""
        q = "-- token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c\nSELECT 1"
        out, summary = redact_sql_query_text(q)
        assert "[REDACTED:jwt]" in out
        assert "jwt" in summary["patterns_matched"]

    def test_scrub_flags_disable(self):
        q = "SELECT * FROM t WHERE x = 'a' AND y = 1"
        out, _ = redact_sql_query_text(q, scrub_string_literals=False, scrub_numeric_literals=False)
        assert "'a'" in out
        assert " 1" in out  # numeric literal preserved


class TestRedactTraceMessage:
    def test_ipv4_redacted(self):
        msg = "Connection from 192.168.1.42 timed out"
        out, summary = redact_trace_message(msg)
        assert "192.168.1.42" not in out
        assert summary["patterns_matched"]["ipv4"] == 1

    def test_email_redacted(self):
        msg = "User notify@example.org failed validation"
        out, _ = redact_trace_message(msg)
        assert "notify@example.org" not in out


# --------------------------------------------------------------------------- #
# Parser                                                                      #
# --------------------------------------------------------------------------- #

class TestExtractTables:
    def test_from_clause(self):
        assert extract_tables("SELECT * FROM users") == ["users"]

    def test_join(self):
        q = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id"
        tables = extract_tables(q)
        assert "users" in tables and "orders" in tables

    def test_qualified_name(self):
        assert extract_tables("SELECT * FROM public.users") == ["users"]

    def test_quoted_identifier(self):
        # Postgres-style double-quoted
        tables = extract_tables('SELECT * FROM "Users"')
        assert "users" in tables

    def test_update_insert_delete(self):
        assert extract_tables("UPDATE orders SET x = 1") == ["orders"]
        assert extract_tables("INSERT INTO users (name) VALUES ('a')") == ["users"]
        assert extract_tables("DELETE FROM sessions WHERE id = 1") == ["sessions"]

    def test_no_table_in_select_constant(self):
        assert extract_tables("SELECT 1") == []


class TestExtractColumns:
    def test_select_clause(self):
        q = "SELECT name, email FROM users"
        cols = extract_columns(q, ["users"])
        assert "users" in cols
        assert "name" in cols["users"]
        assert "email" in cols["users"]

    def test_where_clause(self):
        q = "SELECT * FROM users WHERE active = 1 AND created_at > '2020-01-01'"
        cols = extract_columns(q, ["users"])
        assert "active" in cols["users"]
        # created_at uses qualified-ident regex; should be picked up
        assert "created_at" in cols["users"]

    def test_keywords_filtered(self):
        q = "SELECT name FROM users WHERE name IS NOT NULL"
        cols = extract_columns(q, ["users"])
        # NOT / NULL / IS are keywords — should not appear as columns
        for kw in ("not", "null", "is"):
            assert kw not in cols["users"]

    def test_table_name_excluded(self):
        q = "SELECT users.name FROM users"
        cols = extract_columns(q, ["users"])
        # `users` is the table; only `name` should land
        assert "users" not in cols["users"]
        assert "name" in cols["users"]


class TestParseFile:
    def _write_csv(self, path: Path, rows: list[dict]):
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)

    def test_pg_stat_csv(self, tmp_path):
        path = tmp_path / "pgstat.csv"
        self._write_csv(path, [
            {"query": "SELECT name FROM users WHERE id = 1", "calls": "10", "total_time": "12.5"},
            {"query": "UPDATE orders SET status = 'ok' WHERE id = 2", "calls": "3", "total_time": "5.2"},
        ])
        recs = list(parse_sql_log_file(str(path)))
        assert len(recs) == 2
        assert recs[0].calls == 10
        assert "users" in recs[0].tables
        assert recs[1].calls == 3
        assert "orders" in recs[1].tables

    def test_jsonl(self, tmp_path):
        path = tmp_path / "log.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"query": "SELECT 1"}) + "\n")
            f.write(json.dumps({"query": "SELECT email FROM users", "calls": 5}) + "\n")
        recs = list(parse_sql_log_file(str(path)))
        assert len(recs) == 2
        assert recs[1].calls == 5

    def test_gzip(self, tmp_path):
        path = tmp_path / "log.csv.gz"
        with gzip.open(path, mode="wt", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["query", "calls"])
            w.writeheader()
            w.writerow({"query": "SELECT * FROM products", "calls": "1"})
        recs = list(parse_sql_log_file(str(path)))
        assert len(recs) == 1
        assert "products" in recs[0].tables

    def test_max_rows_caps(self, tmp_path):
        path = tmp_path / "many.csv"
        self._write_csv(path, [
            {"query": f"SELECT * FROM t{i}", "calls": "1"} for i in range(50)
        ])
        recs = list(parse_sql_log_file(str(path), max_rows=10))
        assert len(recs) == 10

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(parse_sql_log_file(str(tmp_path / "nope.csv")))


# --------------------------------------------------------------------------- #
# End-to-end ingest                                                           #
# --------------------------------------------------------------------------- #

@pytest.fixture
def indexed_users(tmp_path):
    """Index a small CSV under dataset name 'users' so runtime ingest
    can map queries against it."""
    csv_path = tmp_path / "users.csv"
    csv_path.write_text(
        "id,name,email,active\n"
        "1,alice,alice@example.com,true\n"
        "2,bob,bob@example.com,false\n"
    )
    storage = str(tmp_path / "store")
    res = index_local(path=str(csv_path), use_ai_summaries=False, name="users", storage_path=storage)
    assert "dataset" in res or "error" not in res, f"index failed: {res}"
    return storage


def _write_log(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "log.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


class TestIngestEndToEnd:
    def test_maps_table_to_dataset(self, tmp_path, indexed_users):
        log = _write_log(tmp_path, [
            {"query": "SELECT name, email FROM users WHERE active = true", "calls": "5", "total_time": "3.2"},
            {"query": "SELECT id FROM users", "calls": "10", "total_time": "1.1"},
        ])
        r = ingest_sql_log_file(str(log), storage_path=indexed_users)
        assert r["ingested_rows"] == 2
        assert r["resolved_to_datasets"] == 1
        assert r["unmapped_queries"] == 0
        ds = r["by_dataset"]["users"]
        assert ds["queries_attributed"] == 2
        # Columns hit should include name, email, active, id (those in schema)
        cols_only = {c.split(".", 1)[1] for c in ds["columns_hit"]}
        assert {"name", "email", "id"}.issubset(cols_only)

    def test_unmapped_when_table_unknown(self, tmp_path, indexed_users):
        log = _write_log(tmp_path, [
            {"query": "SELECT * FROM orders WHERE id = 1", "calls": "1", "total_time": "0.5"},
        ])
        r = ingest_sql_log_file(str(log), storage_path=indexed_users)
        assert r["unmapped_queries"] == 1
        assert r["resolved_to_datasets"] == 0

    def test_redaction_active_by_default(self, tmp_path, indexed_users):
        log = _write_log(tmp_path, [
            {"query": "SELECT * FROM users WHERE email = 'leak@example.com'", "calls": "1", "total_time": "1"},
        ])
        r = ingest_sql_log_file(str(log), storage_path=indexed_users)
        assert r["redaction_summary"]["applied"] is True
        # String-literal scrubbing should fire at minimum
        patterns = r["redaction_summary"]["patterns_matched"]
        assert patterns.get("sql_string_literal", 0) >= 1

    def test_redact_false_passes_through(self, tmp_path, indexed_users):
        log = _write_log(tmp_path, [
            {"query": "SELECT email FROM users WHERE email = 'leak@example.com'", "calls": "1", "total_time": "1"},
        ])
        r = ingest_sql_log_file(str(log), storage_path=indexed_users, redact=False)
        assert r["redaction_summary"]["applied"] is False
        assert r["redaction_summary"]["cells_redacted"] == 0

    def test_upsert_accumulates_calls(self, tmp_path, indexed_users):
        log = _write_log(tmp_path, [
            {"query": "SELECT name FROM users", "calls": "3", "total_time": "1"},
            {"query": "SELECT name FROM users", "calls": "7", "total_time": "2"},
        ])
        r = ingest_sql_log_file(str(log), storage_path=indexed_users)
        assert r["ingested_rows"] == 2
        # Inspect the per-dataset SQLite directly to confirm upsert.
        from jdatamunch_mcp.storage.data_store import DataStore
        store = DataStore(base_path=indexed_users)
        db_path = store.sqlite_path("users")
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT calls, total_time_ms FROM runtime_query_calls "
            "WHERE table_ref='users' AND column_ref='name'"
        ).fetchall()
        conn.close()
        # One row for (table, column=name), calls = 3+7 = 10
        assert len(rows) == 1
        assert rows[0][0] == 10
        assert rows[0][1] >= 3.0  # total_time accumulated

    def test_redaction_log_persisted(self, tmp_path, indexed_users):
        log = _write_log(tmp_path, [
            {"query": "SELECT * FROM users WHERE email = 'a@b.com'", "calls": "1", "total_time": "1"},
        ])
        ingest_sql_log_file(str(log), storage_path=indexed_users)
        from jdatamunch_mcp.storage.data_store import DataStore
        store = DataStore(base_path=indexed_users)
        conn = sqlite3.connect(str(store.sqlite_path("users")))
        rows = conn.execute(
            "SELECT pattern, count FROM runtime_redaction_log WHERE source='sql_log'"
        ).fetchall()
        conn.close()
        assert any(p == "sql_string_literal" for p, _ in rows)

    def test_no_datasets_returns_warning(self, tmp_path):
        log = _write_log(tmp_path, [
            {"query": "SELECT * FROM users", "calls": "1", "total_time": "1"},
        ])
        r = ingest_sql_log_file(str(log), storage_path=str(tmp_path / "empty_store"))
        assert r["resolved_to_datasets"] == 0
        assert "warnings" in r

    def test_max_rows_caps_ingest(self, tmp_path, indexed_users):
        log = _write_log(tmp_path, [
            {"query": f"SELECT name FROM users WHERE id = {i}", "calls": "1", "total_time": "1"}
            for i in range(30)
        ])
        r = ingest_sql_log_file(str(log), storage_path=indexed_users, max_rows=5)
        assert r["ingested_rows"] == 5
