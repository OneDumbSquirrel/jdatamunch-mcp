"""v1.18.0 — suite-parity _meta.verdict on search_data."""

from jdatamunch_mcp.tools.search_data import search_data
from jdatamunch_mcp.verdict import build_verdict, suggest_columns


class TestVerdictUnit:
    def test_ok(self):
        v = build_verdict(result_count=3)
        assert v["state"] == "ok"
        assert v["channels"] == {"lexical": "ok", "semantic": "off"}

    def test_absent_with_suggestions(self):
        v = build_verdict(result_count=0, did_you_mean=["city"])
        assert v["state"] == "absent"
        assert v["did_you_mean"] == ["city"]

    def test_degraded_when_semantic_unavailable(self):
        v = build_verdict(
            result_count=4, semantic_requested=True, semantic_available=False
        )
        assert v["state"] == "degraded"
        assert v["channels"]["semantic"] == "unavailable"

    def test_semantic_channel_ok_when_available(self):
        v = build_verdict(
            result_count=4, semantic_requested=True, semantic_available=True
        )
        assert v["state"] == "ok"
        assert v["channels"]["semantic"] == "ok"

    def test_semantic_only_marks_lexical_off(self):
        v = build_verdict(result_count=2, lexical_used=False)
        assert v["channels"]["lexical"] == "off"

    def test_suggest_columns_matches_name(self):
        cols = [{"name": "city"}, {"name": "age"}, {"name": "score"}]
        assert suggest_columns("city", cols) == ["city"]
        assert suggest_columns("nomatchxyz", cols) == []


class TestVerdictOnSearchData:
    def test_ok_verdict_on_hit(self, indexed_sample):
        res = search_data(dataset="sample", query="city", storage_path=indexed_sample)
        assert res["_meta"]["verdict"]["state"] == "ok"

    def test_absent_verdict_and_did_you_mean(self, indexed_sample):
        # 'cityzzz' matches no column/value but is a near-miss of the 'city' column.
        res = search_data(
            dataset="sample", query="cityzzz_nomatch", storage_path=indexed_sample
        )
        assert res["result"] == []
        v = res["_meta"]["verdict"]
        assert v["state"] == "absent"

    def test_degraded_when_semantic_requested_no_provider(self, indexed_sample, monkeypatch):
        # Force the semantic channel to raise so search_data falls back to
        # keyword-only (the non-semantic_only fallback path) -> degraded.
        import jdatamunch_mcp.tools.search_data as sd

        def _boom(*a, **k):
            raise RuntimeError("no embeddings in test")

        monkeypatch.setattr(sd, "_semantic_scores", _boom)
        res = search_data(
            dataset="sample", query="city", semantic=True, storage_path=indexed_sample
        )
        assert "error" not in res
        assert res["_meta"]["verdict"]["state"] == "degraded"
        assert res["_meta"]["verdict"]["channels"]["semantic"] == "unavailable"
