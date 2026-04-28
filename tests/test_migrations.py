"""Index migration framework: v1 → v2 (A11)."""

from jdatamunch_mcp.storage.migrations import migrate_to_current
from jdatamunch_mcp.storage.data_store import INDEX_VERSION


def _legacy_v1_doc():
    return {
        "dataset": "legacy",
        "source_path": "/x/legacy.csv",
        "source_format": "csv",
        "source_hash": "sha256:deadbeef",
        "source_size_bytes": 100,
        "indexed_at": "2025-01-01T00:00:00",
        "index_version": 1,
        "row_count": 5,
        "column_count": 1,
        "encoding": "utf-8",
        "delimiter": ",",
        "columns": [{
            "name": "x",
            "position": 0,
            "type": "integer",
            "count": 5,
            "null_count": 0,
            "null_pct": 0.0,
            "cardinality": 5,
            "is_unique": True,
            "is_primary_key_candidate": True,
            "min": 1, "max": 5, "mean": 3.0, "median": 3,
            "sample_values": [1, 2, 3, 4, 5],
            "value_index": {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1},
            "top_values": None,
            "datetime_min": None, "datetime_max": None, "datetime_format": None,
            "ai_summary": "Integer column",
        }],
        "sqlite_relative_path": "data.sqlite",
        "dataset_summary": "tiny",
    }


def test_migrate_v1_adds_new_fields():
    doc = _legacy_v1_doc()
    out = migrate_to_current(doc)
    assert out["index_version"] == INDEX_VERSION
    col = out["columns"][0]
    # New fields should all be present, populated with safe defaults.
    for field in ("std_dev", "variance", "quantiles", "cardinality_estimated",
                  "cardinality_approx", "type_confidence", "type_violation_count",
                  "type_violation_samples", "semantic_type", "semantic_confidence"):
        assert field in col, f"missing migrated field: {field}"
    assert col["cardinality_estimated"] is False
    assert col["type_confidence"] == 1.0
    assert col["semantic_type"] is None


def test_migrate_idempotent():
    doc = _legacy_v1_doc()
    once = migrate_to_current(doc)
    twice = migrate_to_current(once)
    assert once == twice
