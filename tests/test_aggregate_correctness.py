"""Aggregate correctness vs Python reference (A12).

Avoids pandas dependency by using statistics module — computes the
expected aggregations the same way pandas would, then asserts the
SQLite-backed aggregate tool agrees.
"""

import csv
import statistics

import pytest

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.aggregate import aggregate


@pytest.fixture
def indexed_numbers(tmp_path):
    csv_path = tmp_path / "nums.csv"
    rows = [(i, i * 2, "even" if i % 2 == 0 else "odd") for i in range(1, 21)]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "doubled", "parity"])
        w.writerows(rows)
    storage = tmp_path / "data-index"
    storage.mkdir()
    index_local(path=str(csv_path), name="nums", storage_path=str(storage))
    return str(storage)


def test_count_matches(indexed_numbers):
    res = aggregate(
        dataset="nums",
        aggregations=[{"column": "*", "function": "count", "alias": "n"}],
        storage_path=indexed_numbers,
    )
    assert res["result"]["groups"][0]["n"] == 20


def test_sum_avg_match(indexed_numbers):
    res = aggregate(
        dataset="nums",
        aggregations=[
            {"column": "doubled", "function": "sum", "alias": "s"},
            {"column": "doubled", "function": "avg", "alias": "m"},
        ],
        storage_path=indexed_numbers,
    )
    expected_sum = sum(range(1, 21)) * 2
    expected_avg = expected_sum / 20
    g = res["result"]["groups"][0]
    assert g["s"] == expected_sum
    assert abs(g["m"] - expected_avg) < 1e-9


def test_group_by_parity(indexed_numbers):
    res = aggregate(
        dataset="nums",
        aggregations=[{"column": "*", "function": "count", "alias": "n"}],
        group_by=["parity"],
        storage_path=indexed_numbers,
    )
    counts = {g["parity"]: g["n"] for g in res["result"]["groups"]}
    assert counts == {"even": 10, "odd": 10}
