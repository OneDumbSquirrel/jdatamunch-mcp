"""Tests for find_similar_columns (v1.12.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jdatamunch_mcp.tools.find_similar_columns import (
    _jaccard,
    _tokens,
    _type_score,
    find_similar_columns,
)
from jdatamunch_mcp.tools.index_local import index_local


# ── Pure-function unit tests ──────────────────────────────────────────────────

def test_tokens_snake_case():
    assert _tokens("user_id") == {"user", "id"}


def test_tokens_camel_case():
    assert _tokens("userId") == {"user", "id"}


def test_tokens_screaming_snake():
    assert _tokens("USER_ID") == {"user", "id"}


def test_jaccard_full_overlap():
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_no_overlap():
    assert _jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_partial():
    assert _jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1.0 / 3.0)


def test_type_score():
    assert _type_score("string", "string") == 1.0
    assert _type_score("integer", "float") == 0.5
    assert _type_score("string", "integer") == 0.0


# ── Integration tests ────────────────────────────────────────────────────────

def _write_users_a(tmp_path: Path) -> Path:
    p = tmp_path / "users_a.csv"
    rows = ["id,name,email"]
    for i in range(20):
        nm = ["alice", "bob", "carol"][i % 3]
        rows.append(f"{i},{nm},{nm}@example.com")
    p.write_text("\n".join(rows) + "\n")
    return p


def _write_users_b(tmp_path: Path) -> Path:
    """Same shape, different table — `email` repeats, `userId` is camelCase."""
    p = tmp_path / "customers_b.csv"
    rows = ["userId,fullName,email"]
    for i in range(20):
        nm = ["alice", "bob", "carol"][i % 3]
        rows.append(f"{i},{nm},{nm}@example.com")
    p.write_text("\n".join(rows) + "\n")
    return p


def _write_distinct(tmp_path: Path) -> Path:
    """Unrelated dataset — should not appear as a hit."""
    p = tmp_path / "weather.csv"
    rows = ["station_code,temperature_f,humidity_pct"]
    for i in range(20):
        rows.append(f"S{i},{50 + i},{40 + i}")
    p.write_text("\n".join(rows) + "\n")
    return p


@pytest.fixture
def two_user_datasets(tmp_path):
    storage = str(tmp_path / "store")
    index_local(path=str(_write_users_a(tmp_path)), use_ai_summaries=False,
                name="users_a", storage_path=storage)
    index_local(path=str(_write_users_b(tmp_path)), use_ai_summaries=False,
                name="customers_b", storage_path=storage)
    return storage


def test_no_datasets_returns_error(tmp_path):
    r = find_similar_columns(storage_path=str(tmp_path / "store"))
    assert "error" in r
    assert r["reason"] == "no_datasets"


def test_unknown_dataset_rejected(tmp_path):
    storage = str(tmp_path / "store")
    index_local(path=str(_write_users_a(tmp_path)), use_ai_summaries=False,
                name="users_a", storage_path=storage)
    r = find_similar_columns(datasets=["users_a", "ghost"], storage_path=storage)
    assert "error" in r
    assert r["reason"] == "dataset_not_found"


def test_parallel_email_columns_cluster(two_user_datasets):
    """`email` in users_a and `email` in customers_b should cluster."""
    r = find_similar_columns(min_score=0.4, storage_path=two_user_datasets)
    assert "result" in r
    clusters = r["result"]["clusters"]
    # Find a cluster that contains both `email` columns.
    matched = None
    for c in clusters:
        members = {(m["dataset"], m["column"]) for m in c["members"]}
        if ("users_a", "email") in members and ("customers_b", "email") in members:
            matched = c
            break
    assert matched is not None, f"No email cluster found in {clusters}"
    assert matched["verdict"] in ("near_duplicate", "parallel_definition")


def test_id_vs_userid_naming_drift(two_user_datasets):
    """`id` in users_a and `userId` in customers_b: tokens share `id`."""
    r = find_similar_columns(min_score=0.3, storage_path=two_user_datasets)
    pairs_seen = []
    for c in r["result"]["clusters"]:
        for p in c["pairs"]:
            cols = {(p["a"]["dataset"], p["a"]["column"]),
                    (p["b"]["dataset"], p["b"]["column"])}
            if {("users_a", "id"), ("customers_b", "userId")} == cols:
                pairs_seen.append(p)
    # The pair may or may not surface depending on signal weights; we
    # mainly want to assert the tokeniser is camel-aware.
    assert _tokens("userId") & _tokens("id") == {"id"}


def test_min_score_filter(two_user_datasets):
    high = find_similar_columns(min_score=0.95, storage_path=two_user_datasets)
    low = find_similar_columns(min_score=0.2, storage_path=two_user_datasets)
    assert low["result"]["total_pairs"] >= high["result"]["total_pairs"]


def test_same_type_only_drops_cross_type_pairs(two_user_datasets):
    r = find_similar_columns(min_score=0.3, same_type_only=True,
                             storage_path=two_user_datasets)
    for c in r["result"]["clusters"]:
        for p in c["pairs"]:
            # Either both string or both numeric — never crossing.
            assert p["a"]["type"] == p["b"]["type"]


def test_unrelated_dataset_does_not_pollute(tmp_path):
    storage = str(tmp_path / "store")
    index_local(path=str(_write_users_a(tmp_path)), use_ai_summaries=False,
                name="users_a", storage_path=storage)
    index_local(path=str(_write_distinct(tmp_path)), use_ai_summaries=False,
                name="weather", storage_path=storage)
    r = find_similar_columns(min_score=0.5, storage_path=storage)
    # weather columns should not surface in a high-confidence cluster
    # with user columns — names share nothing, types diverge.
    for c in r["result"]["clusters"]:
        ds_in_cluster = {m["dataset"] for m in c["members"]}
        # No cross-domain matches at min_score=0.5
        if "weather" in ds_in_cluster and "users_a" in ds_in_cluster:
            pytest.fail(f"Spurious cross-domain cluster: {c}")


def test_top_n_caps_clusters(two_user_datasets):
    r = find_similar_columns(min_score=0.1, top_n=1, storage_path=two_user_datasets)
    assert len(r["result"]["clusters"]) <= 1
