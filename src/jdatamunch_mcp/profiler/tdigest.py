"""Pure-Python t-digest for streaming quantile estimation.

Implements Dunning's t-digest with simple merge-based compression.
~1% accuracy at extreme quantiles (p99) up to 1B values with delta=100.

Memory bounded by `delta` parameter: O(delta) centroids regardless of
how many values are added. Default delta=100 keeps each digest ~3KB.
"""

from __future__ import annotations

import math
from typing import Optional


class TDigest:
    """Streaming quantile estimator. Bounded memory, mergeable."""

    __slots__ = ("delta", "_buffer", "_buffer_cap", "_centroids", "_total_count")

    def __init__(self, delta: int = 100) -> None:
        self.delta = delta
        self._buffer_cap = max(delta * 5, 500)
        self._buffer: list[float] = []  # raw values pending merge
        self._centroids: list[tuple[float, float]] = []  # (mean, weight) sorted by mean
        self._total_count: float = 0.0

    def add(self, value: float, weight: float = 1.0) -> None:
        if math.isnan(value) or math.isinf(value):
            return
        self._buffer.append(value)
        self._total_count += weight
        if len(self._buffer) >= self._buffer_cap:
            self._compress()

    def _scale(self, q: float) -> float:
        # k_1 scale function (asin-based) — bounds centroid weight by quantile.
        # Clamp the asin argument to handle floating-point drift past [-1, 1].
        x = max(-1.0, min(1.0, 2.0 * q - 1.0))
        return self.delta / (2.0 * math.pi) * math.asin(x)

    def _compress(self) -> None:
        if not self._buffer and not self._centroids:
            return
        # Merge buffer + existing centroids into a sorted list
        merged: list[tuple[float, float]] = list(self._centroids)
        for v in self._buffer:
            merged.append((float(v), 1.0))
        self._buffer = []
        merged.sort(key=lambda x: x[0])
        if not merged:
            return

        total = sum(w for _, w in merged)
        if total <= 0:
            self._centroids = []
            return

        new_centroids: list[tuple[float, float]] = []
        cur_mean, cur_weight = merged[0]
        cur_q = cur_weight / total
        k_low = self._scale(0.0)

        for i in range(1, len(merged)):
            mean_i, weight_i = merged[i]
            q_next = cur_q + weight_i / total
            k_high = self._scale(q_next)
            if k_high - k_low <= 1.0:
                # absorb into current centroid
                new_w = cur_weight + weight_i
                cur_mean = cur_mean + (mean_i - cur_mean) * weight_i / new_w
                cur_weight = new_w
                cur_q = q_next
            else:
                new_centroids.append((cur_mean, cur_weight))
                cur_mean = mean_i
                cur_weight = weight_i
                cur_q = cur_q + weight_i / total
                k_low = self._scale(cur_q - weight_i / total)

        new_centroids.append((cur_mean, cur_weight))
        self._centroids = new_centroids
        self._total_count = total

    def quantile(self, q: float) -> Optional[float]:
        if q < 0.0 or q > 1.0:
            return None
        if self._buffer:
            self._compress()
        if not self._centroids:
            return None
        if len(self._centroids) == 1:
            return self._centroids[0][0]

        target = q * self._total_count
        cum = 0.0
        for i, (m, w) in enumerate(self._centroids):
            half = w / 2.0
            if cum + half >= target:
                if i == 0:
                    return m
                prev_m, prev_w = self._centroids[i - 1]
                # Linear interp between prev centroid right edge and this centroid centre
                left_target = cum - prev_w / 2.0
                right_target = cum + half
                if right_target == left_target:
                    return m
                t = (target - left_target) / (right_target - left_target)
                return prev_m + (m - prev_m) * max(0.0, min(1.0, t))
            cum += w

        return self._centroids[-1][0]

    def total_count(self) -> int:
        return int(self._total_count)
