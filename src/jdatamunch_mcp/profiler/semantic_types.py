"""Semantic column type detection.

After primitive type-rank assignment, run a battery of detectors
against the column's sample values + name. Each detector reports
(semantic_type, confidence) on a [0, 1] scale.

Confidence reflects what fraction of non-null samples matched the
pattern. Detectors only fire above their internal threshold.
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Regex catalog (compiled once at import)
# ---------------------------------------------------------------------------

_RX_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_RX_URL = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
_RX_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RX_CURRENCY_ISO = re.compile(r"^[A-Z]{3}$")
_RX_PHONE_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_RX_IPV4 = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)$"
)
_RX_IPV6 = re.compile(r"^[0-9a-fA-F:]+$")
_RX_COUNTRY_ISO2 = re.compile(r"^[A-Z]{2}$")
_RX_ZIP_US = re.compile(r"^\d{5}(?:-\d{4})?$")
_RX_PERCENT = re.compile(r"^-?\d+(?:\.\d+)?\s*%$")

_BOOL_VALUES = frozenset([
    "true", "false", "t", "f", "yes", "no", "y", "n", "0", "1",
])

_CURRENCY_HINT_NAMES = frozenset([
    "currency", "ccy", "iso_currency", "currency_code",
])
_COUNTRY_HINT_NAMES = frozenset([
    "country", "country_code", "iso_country", "nationality",
])
_LAT_HINT_NAMES = frozenset(["lat", "latitude", "y_coord"])
_LON_HINT_NAMES = frozenset(["lon", "lng", "long", "longitude", "x_coord"])
_ZIP_HINT_NAMES = frozenset(["zip", "zipcode", "postal", "postal_code", "postcode"])

_MIN_SAMPLES = 3
_FIRE_THRESHOLD = 0.85  # ≥85% match rate to claim semantic type


def _name_tokens(name: str) -> set:
    """Split a column name into lowercase tokens."""
    s = name.lower()
    for ch in (" ", "-", ".", "/"):
        s = s.replace(ch, "_")
    return set(t for t in s.split("_") if t)


def _ratio(samples: list, predicate) -> float:
    if not samples:
        return 0.0
    hits = sum(1 for s in samples if predicate(s))
    return hits / len(samples)


def _is_lat(s: str) -> bool:
    try:
        v = float(s)
        return -90.0 <= v <= 90.0
    except ValueError:
        return False


def _is_lon(s: str) -> bool:
    try:
        v = float(s)
        return -180.0 <= v <= 180.0
    except ValueError:
        return False


def _is_ipv6(s: str) -> bool:
    if ":" not in s:
        return False
    if not _RX_IPV6.match(s):
        return False
    parts = s.split(":")
    return 3 <= len(parts) <= 8 and all(len(p) <= 4 for p in parts)


def detect_semantic_type(
    primitive_type: str,
    samples: list,
    column_name: str,
) -> tuple[Optional[str], float]:
    """Return (semantic_type, confidence) or (None, 0.0).

    primitive_type: 'integer' | 'float' | 'datetime' | 'string'
    samples: list of stripped, non-null string values
    column_name: original column name (used as a tie-breaker hint)
    """
    if not samples or len(samples) < _MIN_SAMPLES:
        return None, 0.0

    name_tokens = _name_tokens(column_name)

    # Pure-string detectors --------------------------------------------------
    if primitive_type == "string":
        candidates: list[tuple[str, float]] = []

        r = _ratio(samples, lambda s: bool(_RX_EMAIL.match(s)))
        if r >= _FIRE_THRESHOLD:
            candidates.append(("email", r))

        r = _ratio(samples, lambda s: bool(_RX_URL.match(s)))
        if r >= _FIRE_THRESHOLD:
            candidates.append(("url", r))

        r = _ratio(samples, lambda s: bool(_RX_UUID.match(s)))
        if r >= _FIRE_THRESHOLD:
            candidates.append(("uuid", r))

        r = _ratio(samples, lambda s: bool(_RX_PHONE_E164.match(s)))
        if r >= _FIRE_THRESHOLD:
            candidates.append(("phone_e164", r))

        r = _ratio(samples, lambda s: bool(_RX_IPV4.match(s)))
        if r >= _FIRE_THRESHOLD:
            candidates.append(("ipv4", r))

        r = _ratio(samples, _is_ipv6)
        if r >= _FIRE_THRESHOLD:
            candidates.append(("ipv6", r))

        # ISO country code: needs name hint (avoid colliding w/ generic 2-char codes)
        r = _ratio(samples, lambda s: bool(_RX_COUNTRY_ISO2.match(s)))
        if r >= _FIRE_THRESHOLD and name_tokens & _COUNTRY_HINT_NAMES:
            candidates.append(("iso_country", r))

        # ISO currency: needs name hint
        r = _ratio(samples, lambda s: bool(_RX_CURRENCY_ISO.match(s)))
        if r >= _FIRE_THRESHOLD and name_tokens & _CURRENCY_HINT_NAMES:
            candidates.append(("iso_currency", r))

        # US ZIP: needs name hint or universal match (mostly unambiguous due to digits)
        r = _ratio(samples, lambda s: bool(_RX_ZIP_US.match(s)))
        if r >= _FIRE_THRESHOLD and (name_tokens & _ZIP_HINT_NAMES):
            candidates.append(("zip_us", r))

        # Percentage strings ("12%", "0.5 %")
        r = _ratio(samples, lambda s: bool(_RX_PERCENT.match(s)))
        if r >= _FIRE_THRESHOLD:
            candidates.append(("percentage", r))

        # Boolean text
        r = _ratio(samples, lambda s: s.lower() in _BOOL_VALUES)
        if r >= _FIRE_THRESHOLD:
            candidates.append(("boolean_text", r))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_type, best_conf = candidates[0]
            return best_type, round(best_conf, 3)

    # Numeric detectors ------------------------------------------------------
    if primitive_type in ("integer", "float"):
        # Lat/lon: name hint required to avoid mislabeling generic floats
        if name_tokens & _LAT_HINT_NAMES:
            r = _ratio(samples, _is_lat)
            if r >= _FIRE_THRESHOLD:
                return "lat", round(r, 3)
        if name_tokens & _LON_HINT_NAMES:
            r = _ratio(samples, _is_lon)
            if r >= _FIRE_THRESHOLD:
                return "lon", round(r, 3)

        # US zip stored as integer
        if name_tokens & _ZIP_HINT_NAMES:
            r = _ratio(samples, lambda s: bool(_RX_ZIP_US.match(s)))
            if r >= _FIRE_THRESHOLD:
                return "zip_us", round(r, 3)

    return None, 0.0
