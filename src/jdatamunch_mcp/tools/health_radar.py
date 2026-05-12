"""Six-axis data-health radar + diff helper (v1.11.0).

Compresses dataset-quality signals into normalised 0-100 scores per
axis, plus a composite + letter grade. Same shape works as a *state*
snapshot or as a *delta* between two snapshots — run on yesterday's
profile, run on today's, diff the payloads for nightly change reports.

The radar pulls inputs from existing index.json + runtime tables (no
new heavy work):

| Axis              | Source                                         |
|-------------------|------------------------------------------------|
| null_health       | 100 - avg(null_pct) across columns             |
| type_confidence   | avg(type_confidence) across columns x 100      |
| cardinality_health| penalty per constant column                    |
| pk_presence       | any PK-candidate? full score : half score      |
| semantic_coverage | semantic_type detected / typeable candidates   |
| schema_stability  | drift-free between first/last history snapshot |
| runtime_coverage  | (optional) % of columns with runtime hits      |

Higher score = healthier. Mirrors jcm's six-axis radar with the
healthy-by-default optional 7th axis convention so callers that haven't
ingested traces don't see a composite penalty.
"""

from __future__ import annotations

from typing import Optional


_AXES: tuple[str, ...] = (
    "null_health",
    "type_confidence",
    "cardinality_health",
    "pk_presence",
    "semantic_coverage",
    "schema_stability",
)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _score_null_health(avg_null_pct: float) -> float:
    """0% null avg -> 100; 50% -> 50; 100% -> 0."""
    return _clamp(100.0 - avg_null_pct)


def _score_type_confidence(avg_confidence: float) -> float:
    """avg confidence in [0, 1] mapped to [0, 100]."""
    return _clamp(avg_confidence * 100.0)


def _score_cardinality_health(constant_columns: int, total_columns: int) -> float:
    """0 constant -> 100; 20% constant -> 0."""
    if total_columns <= 0:
        return 100.0
    ratio = constant_columns / total_columns
    return _clamp(100.0 - 500.0 * ratio)


def _score_pk_presence(has_pk: bool) -> float:
    return 100.0 if has_pk else 50.0


def _score_semantic_coverage(typed: int, candidates: int) -> float:
    """typed / candidates (string + numeric columns) x 100. No candidates -> 100."""
    if candidates <= 0:
        return 100.0
    return _clamp((typed / candidates) * 100.0)


def _score_schema_stability(drift_free: Optional[bool]) -> Optional[float]:
    """drift_free=True -> 100; False -> 50; None (no history) -> axis omitted."""
    if drift_free is None:
        return None
    return 100.0 if drift_free else 50.0


def _score_runtime_coverage(coverage_pct: float) -> float:
    """% of columns with at least one runtime hit. Direct linear mapping."""
    return _clamp(coverage_pct)


def _letter_grade(composite: float) -> str:
    if composite >= 90:
        return "A"
    if composite >= 80:
        return "B"
    if composite >= 70:
        return "C"
    if composite >= 60:
        return "D"
    return "F"


def compute_radar(
    *,
    avg_null_pct: float,
    avg_type_confidence: float,
    constant_columns: int,
    total_columns: int,
    has_pk: bool,
    typed_columns: int,
    typeable_candidates: int,
    drift_free: Optional[bool] = None,
    runtime_coverage_pct: Optional[float] = None,
) -> dict:
    """Compute the six (or seven) axis radar from raw signal inputs.

    Args:
        avg_null_pct: Mean null percentage across columns (0-100).
        avg_type_confidence: Mean type-inference confidence across columns
            (0.0-1.0). Pass 1.0 for legacy profiles without confidence.
        constant_columns: Count of cardinality==1 columns.
        total_columns: Denominator for the cardinality axis.
        has_pk: True iff any column is a primary-key candidate.
        typed_columns: Columns with a semantic_type detected.
        typeable_candidates: Denominator — count of string/integer/float
            columns where semantic typing could fire.
        drift_free: None when no history exists (axis omitted); True when
            first and last snapshots have matching column names; False
            when they diverge.
        runtime_coverage_pct: Optional 7th axis. Percentage of columns
            with at least one row in runtime_query_calls. None => omitted.

    Returns:
        ``{axes, composite, grade, omitted_axes}``.
    """
    axes: dict[str, dict] = {
        "null_health": {
            "score": _score_null_health(avg_null_pct),
            "raw": round(avg_null_pct, 2),
        },
        "type_confidence": {
            "score": _score_type_confidence(avg_type_confidence),
            "raw": round(avg_type_confidence, 3),
        },
        "cardinality_health": {
            "score": _score_cardinality_health(constant_columns, total_columns),
            "raw_constant": constant_columns,
            "raw_total": total_columns,
        },
        "pk_presence": {
            "score": _score_pk_presence(has_pk),
            "raw": has_pk,
        },
        "semantic_coverage": {
            "score": _score_semantic_coverage(typed_columns, typeable_candidates),
            "raw_typed": typed_columns,
            "raw_candidates": typeable_candidates,
        },
    }

    omitted: list[str] = []
    stability_score = _score_schema_stability(drift_free)
    if stability_score is not None:
        axes["schema_stability"] = {"score": stability_score, "raw": drift_free}
    else:
        omitted.append("schema_stability")

    if runtime_coverage_pct is not None:
        axes["runtime_coverage"] = {
            "score": _score_runtime_coverage(runtime_coverage_pct),
            "raw": round(runtime_coverage_pct, 2),
        }
    else:
        omitted.append("runtime_coverage")

    scored_values = [a["score"] for a in axes.values()]
    composite = round(sum(scored_values) / len(scored_values), 1) if scored_values else 0.0

    return {
        "axes": axes,
        "composite": composite,
        "grade": _letter_grade(composite),
        "omitted_axes": omitted,
    }


def diff_radar(baseline: dict, current: dict) -> dict:
    """Axis-by-axis deltas between two radar payloads. Pure function."""
    threshold = 3.0
    out_axes: dict[str, dict] = {}
    regressions: list[str] = []
    improvements: list[str] = []

    base_axes = baseline.get("axes", {})
    cur_axes = current.get("axes", {})
    all_axis_names = sorted(set(base_axes.keys()) | set(cur_axes.keys()))

    for axis in all_axis_names:
        b = base_axes.get(axis, {}) or {}
        c = cur_axes.get(axis, {}) or {}
        b_score = b.get("score")
        c_score = c.get("score")
        if b_score is None or c_score is None:
            out_axes[axis] = {
                "from": b_score,
                "to": c_score,
                "delta": None,
                "note": "axis missing from one side",
            }
            continue
        delta = round(c_score - b_score, 1)
        out_axes[axis] = {"from": b_score, "to": c_score, "delta": delta}
        if delta <= -threshold:
            regressions.append(axis)
        elif delta >= threshold:
            improvements.append(axis)

    base_composite = baseline.get("composite", 0.0)
    cur_composite = current.get("composite", 0.0)
    composite_delta = round(cur_composite - base_composite, 1)

    base_grade = baseline.get("grade", "?")
    cur_grade = current.get("grade", "?")
    grade_change = (
        f"{base_grade} -> {cur_grade}" if base_grade != cur_grade else f"{cur_grade} (unchanged)"
    )

    return {
        "axis_deltas": out_axes,
        "composite_from": base_composite,
        "composite_to": cur_composite,
        "composite_delta": composite_delta,
        "grade_change": grade_change,
        "regressions": regressions,
        "improvements": improvements,
        "verdict": _verdict(composite_delta, regressions, improvements),
    }


def _verdict(composite_delta: float, regressions: list[str], improvements: list[str]) -> str:
    if abs(composite_delta) < 1.0 and not regressions and not improvements:
        return "no meaningful change"
    if regressions and not improvements:
        return f"REGRESSION on {len(regressions)} axis/axes (composite {composite_delta:+.1f})"
    if improvements and not regressions:
        return f"improvement on {len(improvements)} axis/axes (composite {composite_delta:+.1f})"
    if regressions and improvements:
        return f"mixed: -{len(regressions)} / +{len(improvements)} axes (composite {composite_delta:+.1f})"
    return f"composite {composite_delta:+.1f}"


def diff_data_health_radar(baseline: dict, current: dict) -> dict:
    """MCP tool entry point: takes two radar payloads, returns the diff."""
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        return {
            "error": (
                "diff_data_health_radar requires two radar payload dicts. "
                "Pass the `radar` field from data_health_radar responses."
            )
        }
    if "axes" not in baseline or "axes" not in current:
        return {
            "error": (
                "Both inputs must be radar payloads (need an `axes` field). "
                "Did you pass the full data_health_radar response instead of its `radar` sub-field?"
            )
        }
    return diff_radar(baseline, current)
