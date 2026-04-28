"""Shared null + value normalization across non-CSV parsers (A10).

CSV already yields raw strings that the profiler handles via its
canonical null-token set. Native-typed parsers (JSONL, Parquet, Excel)
must funnel their values through `normalize_native` so they produce the
SAME string representation a CSV cell would.

Contract: for any logical value V, a CSV cell containing V's text and a
JSONL/Parquet/Excel cell containing V's native type must yield bit-equal
profiles after going through this module.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any


# Canonical null tokens (mirrors profiler._NULL_VALUES). Empty string is
# treated as null by the profiler, so we collapse all of these to "".
NULL_TOKENS = frozenset([
    "", "null", "NULL", "none", "None", "N/A", "n/a", "NA", "na",
    "NaN", "nan", "-", ".", "#N/A", "#NA", "#NULL!", "n.a.", "N.A.",
])


def normalize_native(raw: Any, source_format: str = "") -> str:
    """Convert a native-typed cell value to its canonical string form.

    * None / NaN / pd.NA → "" (treated as null downstream).
    * bool → "True" / "False" — matches Python's str(bool).
    * int → decimal literal.
    * float → integer literal if integer-valued, else repr (round-trip safe).
    * datetime → ISO-8601 ("%Y-%m-%dT%H:%M:%S" or "%Y-%m-%d" for date-only).
    * bytes → utf-8 decoded (errors='replace').
    * str → stripped; canonical null tokens collapse to "".
    """
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "True" if raw else "False"
    if isinstance(raw, float):
        if raw != raw:  # NaN
            return ""
        if raw == int(raw) and abs(raw) < 1e16:
            return str(int(raw))
        return repr(raw)
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, _dt.datetime):
        if raw.hour or raw.minute or raw.second or raw.microsecond:
            return raw.strftime("%Y-%m-%dT%H:%M:%S")
        return raw.strftime("%Y-%m-%d")
    if isinstance(raw, _dt.date):
        return raw.strftime("%Y-%m-%d")
    if isinstance(raw, bytes):
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            s = raw.decode("utf-8", errors="replace")
    else:
        s = str(raw)

    stripped = s.strip()
    if stripped in NULL_TOKENS:
        return ""
    return stripped
