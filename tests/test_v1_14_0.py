"""tune_weights tool (v1.14.0) -- tunable search_data ranking weights.

Parity intent: jcm and jdoc both ship a tune_weights tool over their ranker;
jData's ranker (search_data) was hardcoded. This adds the missing knob. There
is no ranking-events ledger here, so tuning is explicit (inspect/set/reset)
rather than learned.
"""

import csv

import pytest

from jdatamunch_mcp import tuning
from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.search_data import search_data
from jdatamunch_mcp.tools.tune_weights import tune_weights


# --------------------------------------------------------------------------- #
# tuning.py unit coverage                                                      #
# --------------------------------------------------------------------------- #
def test_defaults_mirror_registry():
    for name, (default, lo, hi) in tuning.TUNABLE_WEIGHTS.items():
        assert tuning.DEFAULT_WEIGHTS[name] == default
        assert lo <= default <= hi


def test_load_effective_weights_returns_defaults_when_unset(tmp_path):
    eff = tuning.load_effective_weights(storage_path=str(tmp_path))
    assert eff == tuning.DEFAULT_WEIGHTS
    # A pristine scope reports every weight as a default.
    assert set(tuning.weight_sources(storage_path=str(tmp_path)).values()) == {"default"}


def test_validate_overrides_flags_unknown_and_nonnumeric():
    clean, errors = tuning.validate_overrides(
        {"name_exact": 30, "bogus": 1, "value_exact": "x"}
    )
    assert clean == {"name_exact": 30.0}
    assert any("bogus" in e for e in errors)
    assert any("value_exact" in e for e in errors)


def test_clamp_respects_bounds():
    assert tuning.clamp_weight("default_semantic_weight", 5.0) == 1.0
    assert tuning.clamp_weight("default_semantic_weight", -2.0) == 0.0
    assert tuning.clamp_weight("name_exact", 999.0) == 999.0


def test_global_set_load_and_clear_roundtrip(tmp_path):
    storage = str(tmp_path)
    tuning.set_overrides({"name_exact": 42.0}, storage_path=storage)
    assert tuning.load_effective_weights(storage_path=storage)["name_exact"] == 42.0
    assert tuning.weight_sources(storage_path=storage)["name_exact"] == "global"
    cleared = tuning.clear_overrides(storage_path=storage)
    assert cleared == 1
    assert tuning.load_effective_weights(storage_path=storage)["name_exact"] == 20.0


def test_dataset_override_wins_over_global(tmp_path):
    storage = str(tmp_path)
    tuning.set_overrides({"name_exact": 30.0}, storage_path=storage)
    tuning.set_overrides({"name_exact": 99.0}, dataset="sales", storage_path=storage)
    assert tuning.load_effective_weights(storage_path=storage)["name_exact"] == 30.0
    assert tuning.load_effective_weights("sales", storage_path=storage)["name_exact"] == 99.0
    assert tuning.weight_sources("sales", storage_path=storage)["name_exact"] == "dataset"
    # Clearing the dataset scope falls back to the global override.
    tuning.clear_overrides(dataset="sales", storage_path=storage)
    assert tuning.load_effective_weights("sales", storage_path=storage)["name_exact"] == 30.0


def test_corrupt_tuning_file_degrades_to_defaults(tmp_path):
    (tmp_path / "ranking_tuning.json").write_text("{not json", encoding="utf-8")
    assert tuning.load_effective_weights(storage_path=str(tmp_path)) == tuning.DEFAULT_WEIGHTS


# --------------------------------------------------------------------------- #
# tune_weights tool                                                           #
# --------------------------------------------------------------------------- #
def test_inspect_reports_all_weights(tmp_path):
    out = tune_weights(storage_path=str(tmp_path))
    assert out["action"] == "inspect"
    assert out["scope"] == "global"
    assert set(out["weights"]) == set(tuning.TUNABLE_WEIGHTS)
    assert out["weights"]["name_exact"]["value"] == 20.0
    assert out["weights"]["name_exact"]["source"] == "default"
    assert out["overrides"] == {}


def test_set_then_inspect(tmp_path):
    storage = str(tmp_path)
    out = tune_weights(set_weights={"name_exact": 35, "default_semantic_weight": 0.9}, storage_path=storage)
    assert out["action"] == "set"
    assert out["applied"] == {"name_exact": 35.0, "default_semantic_weight": 0.9}
    again = tune_weights(storage_path=storage)
    assert again["weights"]["name_exact"]["value"] == 35.0
    assert again["weights"]["name_exact"]["source"] == "global"
    assert again["overrides"] == {"name_exact": 35.0, "default_semantic_weight": 0.9}


def test_set_clamps_out_of_range(tmp_path):
    out = tune_weights(set_weights={"default_semantic_weight": 5.0}, storage_path=str(tmp_path))
    assert out["applied"]["default_semantic_weight"] == 1.0


def test_invalid_weight_rejected_without_writing(tmp_path):
    storage = str(tmp_path)
    out = tune_weights(set_weights={"nope": 1}, storage_path=storage)
    assert out["error"] == "invalid_weights"
    assert "nope" in out["message"]
    # Nothing should have been persisted.
    assert tune_weights(storage_path=storage)["overrides"] == {}


def test_reset_clears(tmp_path):
    storage = str(tmp_path)
    tune_weights(set_weights={"name_exact": 50}, storage_path=storage)
    out = tune_weights(reset=True, storage_path=storage)
    assert out["action"] == "reset"
    assert out["cleared"] == 1
    assert tune_weights(storage_path=storage)["overrides"] == {}


# --------------------------------------------------------------------------- #
# search_data honors the tuned weights                                        #
# --------------------------------------------------------------------------- #
def _index_two_column_dataset(tmp_path):
    """A dataset where one column matches on NAME and another on VALUE."""
    csv_path = tmp_path / "people.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["status", "note"])
        # 'note' carries the literal value "status" so it wins on value match;
        # 'status' wins on an exact name match. Their relative rank is what the
        # name_exact vs value_exact weights decide.
        w.writerows([("active", "status"), ("active", "status"), ("idle", "other")])
    storage = tmp_path / "store"
    storage.mkdir()
    index_local(path=str(csv_path), name="people", storage_path=str(storage))
    return str(storage)


def test_search_data_default_behavior_unchanged(tmp_path):
    storage = _index_two_column_dataset(tmp_path)
    res = search_data(dataset="people", query="status", storage_path=storage)
    names = [r["name"] for r in res["result"]]
    # Exact name match should outrank the value match under default weights.
    assert names[0] == "status"


def test_tuning_value_weight_reranks_results(tmp_path):
    storage = _index_two_column_dataset(tmp_path)
    # Crank the value-match weights far above the name weights for this dataset.
    tune_weights(
        dataset="people",
        set_weights={"name_exact": 1, "value_exact": 500, "value_substr": 500},
        storage_path=storage,
    )
    res = search_data(dataset="people", query="status", storage_path=storage)
    names = [r["name"] for r in res["result"]]
    # Now the column whose VALUES contain "status" should win.
    assert names[0] == "note"
    # Global default (no dataset override) still ranks by name.
    assert tuning.load_effective_weights(storage_path=storage)["value_exact"] == 8.0
