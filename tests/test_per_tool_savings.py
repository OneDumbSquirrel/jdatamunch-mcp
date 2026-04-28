"""Per-tool token-savings attribution (C5)."""

import csv

import pytest

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.aggregate import aggregate
from jdatamunch_mcp.tools.describe_dataset import describe_dataset
from jdatamunch_mcp.tools.get_session_stats import get_session_stats


def test_per_tool_breakdown_recorded(tmp_path):
    csv_path = tmp_path / "x.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["g", "v"])
        for i in range(100):
            w.writerow(["a" if i % 2 else "b", i])
    storage = tmp_path / "data-index"
    storage.mkdir()

    index_local(path=str(csv_path), name="x", storage_path=str(storage))
    describe_dataset(dataset="x", storage_path=str(storage))
    aggregate(
        dataset="x",
        aggregations=[{"column": "*", "function": "count", "alias": "n"}],
        storage_path=str(storage),
    )

    stats = get_session_stats(storage_path=str(storage))
    per_tool = {entry["tool"]: entry for entry in stats["result"]["per_tool"]}
    assert "index_local" in per_tool
    assert "describe_dataset" in per_tool
    assert "aggregate" in per_tool
    # Each tool recorded at least one call
    for tool in ("index_local", "describe_dataset", "aggregate"):
        assert per_tool[tool]["calls"] >= 1
