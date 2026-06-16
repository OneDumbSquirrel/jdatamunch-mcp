"""Ranking-weight tuning for jdatamunch-mcp.

``search_data`` ranks columns with a small vector of weights (name / value /
type match weights plus the BM25 and semantic blend scales). Before v1.14.0
those weights were hardcoded module constants in ``tools/search_data.py``;
this module makes them tunable and persistable, and is the single source of
truth for their defaults.

Unlike jcodemunch-mcp / jdocmunch-mcp -- whose ``tune_weights`` *learns*
weights from a persisted ranking-events ledger -- jdatamunch-mcp keeps no such
ledger (``call_tracker`` is ephemeral loop-detection only), so tuning here is
explicit: inspect, set, reset. Overrides persist in
``<index_path>/ranking_tuning.json`` as::

    {"global": {weight: value, ...}, "datasets": {"<name>": {weight: value}}}

Resolution order is defaults < global overrides < per-dataset overrides.
``search_data`` resolves the effective vector once per query via
``load_effective_weights``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .config import get_index_path

# name -> (default, min, max). The defaults are the historical search_data
# constants; the bounds keep a tuned value sane (and a blend weight in [0, 1]).
TUNABLE_WEIGHTS: dict[str, tuple[float, float, float]] = {
    "name_exact": (20.0, 0.0, 1000.0),
    "name_substr": (10.0, 0.0, 1000.0),
    "name_word": (5.0, 0.0, 1000.0),
    "ai_summary_word": (3.0, 0.0, 1000.0),
    "value_exact": (8.0, 0.0, 1000.0),
    "value_substr": (4.0, 0.0, 1000.0),
    "type_boost": (2.0, 0.0, 1000.0),
    "bm25_scale": (5.0, 0.0, 1000.0),
    "semantic_scale": (20.0, 0.0, 1000.0),
    "default_semantic_weight": (0.5, 0.0, 1.0),
}

DEFAULT_WEIGHTS: dict[str, float] = {k: v[0] for k, v in TUNABLE_WEIGHTS.items()}

_TUNING_FILENAME = "ranking_tuning.json"


def _tuning_path(storage_path: Optional[str] = None) -> Path:
    return get_index_path(storage_path) / _TUNING_FILENAME


def _read_file(storage_path: Optional[str] = None) -> dict:
    """Load the raw tuning document; return {} on missing or corrupt file."""
    path = _tuning_path(storage_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _write_file(data: dict, storage_path: Optional[str] = None) -> None:
    """Atomically persist the tuning document."""
    path = _tuning_path(storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def clamp_weight(name: str, value: float) -> float:
    """Coerce + clamp a single weight to its registered bounds."""
    _, lo, hi = TUNABLE_WEIGHTS[name]
    return max(lo, min(hi, float(value)))


def validate_overrides(overrides: dict) -> tuple[dict, list[str]]:
    """Split an override dict into (clean_clamped, errors).

    Unknown keys and non-numeric values become errors; everything valid is
    coerced to float and clamped to its bounds.
    """
    clean: dict[str, float] = {}
    errors: list[str] = []
    if not isinstance(overrides, dict):
        return clean, ["set_weights must be an object of {weight: number}"]
    for key, raw in overrides.items():
        if key not in TUNABLE_WEIGHTS:
            errors.append(f"unknown weight {key!r}")
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            errors.append(f"weight {key!r} must be a number, got {raw!r}")
            continue
        clean[key] = clamp_weight(key, value)
    return clean, errors


def get_overrides(dataset: Optional[str] = None, storage_path: Optional[str] = None) -> dict:
    """Return only the persisted overrides for one scope (global if dataset is None)."""
    data = _read_file(storage_path)
    if dataset is None:
        scope = data.get("global", {})
    else:
        scope = data.get("datasets", {}).get(dataset, {})
    return {k: clamp_weight(k, v) for k, v in scope.items() if k in TUNABLE_WEIGHTS}


def load_effective_weights(
    dataset: Optional[str] = None, storage_path: Optional[str] = None
) -> dict:
    """Resolve the effective weight vector: defaults < global < per-dataset."""
    data = _read_file(storage_path)
    effective = dict(DEFAULT_WEIGHTS)
    for key, value in data.get("global", {}).items():
        if key in effective:
            effective[key] = clamp_weight(key, value)
    if dataset is not None:
        for key, value in data.get("datasets", {}).get(dataset, {}).items():
            if key in effective:
                effective[key] = clamp_weight(key, value)
    return effective


def weight_sources(
    dataset: Optional[str] = None, storage_path: Optional[str] = None
) -> dict:
    """Per-weight provenance: 'default' | 'global' | 'dataset'."""
    data = _read_file(storage_path)
    global_keys = data.get("global", {})
    dataset_keys = data.get("datasets", {}).get(dataset, {}) if dataset is not None else {}
    sources: dict[str, str] = {}
    for key in TUNABLE_WEIGHTS:
        if key in dataset_keys:
            sources[key] = "dataset"
        elif key in global_keys:
            sources[key] = "global"
        else:
            sources[key] = "default"
    return sources


def set_overrides(
    clean: dict, dataset: Optional[str] = None, storage_path: Optional[str] = None
) -> dict:
    """Merge an already-validated override dict into one scope and persist.

    Values are clamped again defensively. Returns the applied (clamped) dict.
    """
    applied = {k: clamp_weight(k, v) for k, v in clean.items() if k in TUNABLE_WEIGHTS}
    data = _read_file(storage_path)
    if dataset is None:
        scope = dict(data.get("global", {}))
        scope.update(applied)
        data["global"] = scope
    else:
        datasets = dict(data.get("datasets", {}))
        scope = dict(datasets.get(dataset, {}))
        scope.update(applied)
        datasets[dataset] = scope
        data["datasets"] = datasets
    _write_file(data, storage_path)
    return applied


def clear_overrides(
    dataset: Optional[str] = None, storage_path: Optional[str] = None
) -> int:
    """Remove one scope's overrides. Returns the number of weights cleared."""
    data = _read_file(storage_path)
    if dataset is None:
        cleared = len(data.get("global", {}))
        data["global"] = {}
    else:
        datasets = dict(data.get("datasets", {}))
        cleared = len(datasets.get(dataset, {}))
        datasets.pop(dataset, None)
        data["datasets"] = datasets
    _write_file(data, storage_path)
    return cleared
