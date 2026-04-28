"""Numeric stability of Welford + Neumaier accumulators (A1)."""

import csv
import statistics

import pytest

from jdatamunch_mcp.profiler.column_profiler import _ColAcc, update_acc, finalize_profile


def _profile_one(values):
    acc = _ColAcc(name="x", position=0)
    for v in values:
        update_acc(acc, str(v))
    return finalize_profile(acc)


def test_mean_extreme_magnitude():
    """Naive sum drifts on huge / tiny mixes; compensated sum doesn't."""
    values = [1e9] * 10_000 + [1e-9] * 10_000
    profile = _profile_one(values)
    expected = (1e9 * 10_000 + 1e-9 * 10_000) / 20_000
    assert abs(profile.mean - expected) / expected < 1e-9


def test_std_dev_matches_statistics():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    profile = _profile_one(values)
    expected = statistics.stdev(values)
    assert profile.std_dev is not None
    assert abs(profile.std_dev - expected) < 1e-6


def test_quantiles_present_for_numeric():
    profile = _profile_one(list(range(1, 1001)))
    assert profile.quantiles is not None
    # p50 of 1..1000 is ~500; allow 2% slack from t-digest approximation.
    assert 480 <= profile.quantiles["p50"] <= 520


def test_constant_column_zero_variance():
    profile = _profile_one([7] * 50)
    assert profile.mean == 7.0
    assert profile.std_dev == 0.0


def test_single_value_no_std_dev():
    profile = _profile_one([42])
    assert profile.mean == 42.0
    assert profile.std_dev is None
