"""Approximate aggregate mode (C1)."""

import csv

import pytest

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.aggregate import aggregate


@pytest.fixture
def indexed(tmp_path):
    csv_path = tmp_path / "approx.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "v"])
        for i in range(2000):
            w.writerow([i, i % 200])
    storage = tmp_path / "data-index"
    storage.mkdir()
    index_local(path=str(csv_path), name="approx", storage_path=str(storage))
    return str(storage)


def test_approximate_count_distinct_via_hll(indexed):
    res = aggregate(
        dataset="approx",
        aggregations=[{"column": "v", "function": "count_distinct", "alias": "d"}],
        approximate=True,
        storage_path=indexed,
    )
    assert res["result"]["approximate"] is True
    # True distinct count is 200; HLL ~2% error
    assert 190 <= res["result"]["groups"][0]["d"] <= 210
    conf = res["result"]["confidence"]["d"]
    assert conf in ("hll_2pct",) or conf.startswith("hll")


def test_approximate_median_via_tdigest(indexed):
    res = aggregate(
        dataset="approx",
        aggregations=[{"column": "v", "function": "median", "alias": "m"}],
        approximate=True,
        storage_path=indexed,
    )
    m = res["result"]["groups"][0]["m"]
    # Median of v = i % 200 over 2000 rows is ≈ 99.5; allow loose bounds
    assert 80 <= m <= 120


def test_approximate_avg_with_ci(indexed):
    res = aggregate(
        dataset="approx",
        aggregations=[{"column": "v", "function": "avg", "alias": "a"}],
        approximate=True,
        storage_path=indexed,
    )
    avg = res["result"]["groups"][0]["a"]
    # True avg of v=i%200 over 2000 rows = 99.5
    assert 90 <= avg <= 110


def test_approximate_rejects_group_by(indexed):
    res = aggregate(
        dataset="approx",
        aggregations=[{"column": "v", "function": "count_distinct", "alias": "d"}],
        group_by=["id"],
        approximate=True,
        storage_path=indexed,
    )
    assert "error" in res
