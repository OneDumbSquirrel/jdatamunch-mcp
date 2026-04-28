"""HyperLogLog approximate-distinct-count.

m=2048 registers (p=11). ~1.5KB per HLL. Standard error ~2.3%.
"""

from __future__ import annotations

import hashlib
import math


_P = 11
_M = 1 << _P  # 2048
_M_INV = 1.0 / _M
# Empirical alpha for m=2048
_ALPHA = 0.7213 / (1.0 + 1.079 / _M)


class HyperLogLog:
    """Streaming approximate distinct-cardinality counter."""

    __slots__ = ("registers",)

    def __init__(self) -> None:
        self.registers = bytearray(_M)

    def add(self, value: str) -> None:
        if not value:
            return
        h = int.from_bytes(
            hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(),
            "big",
        )
        idx = h >> (64 - _P)
        w = (h << _P) & ((1 << 64) - 1)
        if w == 0:
            rho = 64 - _P + 1
        else:
            # leading zeros + 1
            rho = (64 - w.bit_length()) + 1
        if rho > self.registers[idx]:
            self.registers[idx] = rho

    def estimate(self) -> int:
        regs = self.registers
        s = 0.0
        zeros = 0
        for r in regs:
            s += 2.0 ** -r
            if r == 0:
                zeros += 1
        e = _ALPHA * _M * _M / s

        # Small-range correction (linear counting)
        if e <= 2.5 * _M and zeros > 0:
            e = _M * math.log(_M / zeros)
        # Large-range correction (64-bit hashes — rarely triggers in practice)
        return int(round(e))
