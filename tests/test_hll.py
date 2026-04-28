"""Cardinality accuracy for HyperLogLog (A3)."""

from jdatamunch_mcp.profiler.hll import HyperLogLog


def test_small_cardinality_exact_via_linear_counting():
    hll = HyperLogLog()
    for i in range(100):
        hll.add(f"v{i}")
    est = hll.estimate()
    # Small-range correction (linear counting) should be near-exact.
    assert 90 <= est <= 110


def test_large_cardinality_within_3pct():
    hll = HyperLogLog()
    n = 1_000_000
    for i in range(n):
        hll.add(f"item_{i}")
    est = hll.estimate()
    err = abs(est - n) / n
    assert err < 0.03, f"HLL estimate {est} off by {err:.3%}"


def test_duplicates_do_not_inflate():
    hll = HyperLogLog()
    for _ in range(10_000):
        hll.add("same")
    assert hll.estimate() <= 5  # one distinct, allow tiny noise
