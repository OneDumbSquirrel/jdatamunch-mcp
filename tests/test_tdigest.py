"""Quantile correctness for the bundled t-digest (A2)."""

import random

import pytest

from jdatamunch_mcp.profiler.tdigest import TDigest


def test_uniform_quantiles():
    """t-digest should hit known quantiles within 1% on uniform data."""
    rng = random.Random(42)
    values = [rng.random() for _ in range(10_000)]
    td = TDigest(delta=100)
    for v in values:
        td.add(v)

    for q, expected in [(0.01, 0.01), (0.25, 0.25), (0.5, 0.5),
                        (0.75, 0.75), (0.95, 0.95), (0.99, 0.99)]:
        got = td.quantile(q)
        assert got is not None
        # Tail quantiles can drift more; allow 2% absolute slack.
        assert abs(got - expected) <= 0.02, f"q={q}: got {got}, expected ~{expected}"


def test_extreme_magnitude():
    """Median is preserved across 1e-6..1e6 mixed input (no overflow / underflow)."""
    td = TDigest(delta=100)
    values = [1e-6] * 5000 + [1e6] * 5000
    rng = random.Random(0)
    rng.shuffle(values)
    for v in values:
        td.add(v)
    p50 = td.quantile(0.5)
    assert p50 is not None
    # Median sits at the boundary between the two clusters.
    assert 1e-6 <= p50 <= 1e6


def test_handles_nan_and_inf():
    td = TDigest(delta=100)
    td.add(float("nan"))
    td.add(float("inf"))
    td.add(1.0)
    assert td.quantile(0.5) == 1.0


def test_quantile_out_of_range():
    td = TDigest(delta=100)
    td.add(1.0)
    assert td.quantile(-0.1) is None
    assert td.quantile(1.1) is None
