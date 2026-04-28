"""Coarse domain classification in summarize_dataset (C4)."""

from jdatamunch_mcp.summarizer import summarize_dataset


def _col(name, type_="string", **kw):
    base = {
        "name": name,
        "type": type_,
        "count": 100,
        "null_count": 0,
        "null_pct": 0.0,
        "cardinality": 50,
    }
    base.update(kw)
    return base


def test_geo_domain_via_lat_lon():
    cols = [_col("latitude", "float", semantic_type="lat"),
            _col("longitude", "float", semantic_type="lon")]
    s = summarize_dataset("x", cols, 100, "csv", 1024)
    assert "geo" in s.lower()


def test_financial_domain_via_currency_semantic():
    cols = [_col("amount", "float"),
            _col("ccy", "string", semantic_type="iso_currency"),
            _col("invoice_id", "string")]
    s = summarize_dataset("x", cols, 100, "csv", 1024)
    assert "financial" in s.lower()


def test_log_domain_via_keywords():
    cols = [_col("timestamp", "datetime"),
            _col("level", "string"),
            _col("logger", "string")]
    s = summarize_dataset("x", cols, 100, "csv", 1024)
    assert "log" in s.lower()


def test_temporal_fallback_when_only_datetime_present():
    cols = [_col("created_at", "datetime"),
            _col("title", "string")]
    s = summarize_dataset("x", cols, 100, "csv", 1024)
    assert "temporal" in s.lower()


def test_no_domain_for_neutral_data():
    cols = [_col("a", "string"), _col("b", "integer")]
    s = summarize_dataset("x", cols, 100, "csv", 1024)
    assert "domain" not in s.lower()
