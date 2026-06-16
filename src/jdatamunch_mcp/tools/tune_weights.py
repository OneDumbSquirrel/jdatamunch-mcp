"""tune_weights -- inspect / set / reset the search_data ranking weights."""

from __future__ import annotations

import time
from typing import Optional

from ..tuning import (
    DEFAULT_WEIGHTS,
    TUNABLE_WEIGHTS,
    clear_overrides,
    get_overrides,
    load_effective_weights,
    set_overrides,
    validate_overrides,
    weight_sources,
)


def tune_weights(
    dataset: Optional[str] = None,
    set_weights: Optional[dict] = None,
    reset: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Inspect, set, or reset the column-search ranking weights.

    ``search_data`` scores columns with a small weight vector -- name / value /
    type match weights plus the BM25 and semantic blend scales. This tool
    exposes that vector so you can re-tune which signals dominate a search;
    ``search_data`` honors the effective weights at query time.

    Scope: pass ``dataset`` to tune one dataset, or omit it to tune the global
    default. Per-dataset overrides win over global, which wins over the
    built-in defaults.

    Modes (precedence: reset > set > inspect):
      * ``reset=True``     -- clear this scope's overrides (back to inherited).
      * ``set_weights``    -- apply weight overrides (validated + clamped).
      * neither            -- return the effective weights + per-weight source.

    Tunable weights: name_exact, name_substr, name_word, ai_summary_word,
    value_exact, value_substr, type_boost, bm25_scale, semantic_scale,
    default_semantic_weight.

    Note: jdatamunch-mcp keeps no ranking-events ledger (``call_tracker`` is
    ephemeral loop-detection only), so weights are tuned explicitly here --
    jcodemunch-mcp / jdocmunch-mcp learn theirs from a ledger.
    """
    t0 = time.perf_counter()
    scope = "global" if dataset is None else f"dataset:{dataset}"
    applied: dict = {}
    cleared = 0

    if reset:
        cleared = clear_overrides(dataset, storage_path)
        action = "reset"
    elif set_weights:
        clean, errors = validate_overrides(set_weights)
        if errors:
            return {
                "error": "invalid_weights",
                "message": "; ".join(errors),
                "tunable": sorted(TUNABLE_WEIGHTS),
            }
        applied = set_overrides(clean, dataset, storage_path)
        action = "set"
    else:
        action = "inspect"

    effective = load_effective_weights(dataset, storage_path)
    sources = weight_sources(dataset, storage_path)

    weights = {
        name: {
            "value": effective[name],
            "default": DEFAULT_WEIGHTS[name],
            "min": TUNABLE_WEIGHTS[name][1],
            "max": TUNABLE_WEIGHTS[name][2],
            "source": sources[name],
        }
        for name in sorted(TUNABLE_WEIGHTS)
    }

    out: dict = {
        "action": action,
        "scope": scope,
        "weights": weights,
        "overrides": get_overrides(dataset, storage_path),
        "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 2)},
    }
    if action == "set":
        out["applied"] = applied
    elif action == "reset":
        out["cleared"] = cleared
    return out
